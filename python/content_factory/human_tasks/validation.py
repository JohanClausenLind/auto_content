"""Human task submission validation (14.3): probes + ASR-vs-script diff with a configurable
tolerance. Improvisation within tolerance offers accept-as-performed; a mismatch shows the diff
and never silently accepts. Takes are immutable; a rejected take never deletes prior takes."""

from __future__ import annotations

import difflib
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from content_factory.audio.normalize import tokenize_words

Transcriber = Callable[[Path], str]
"""ASR: audio file → transcript text. Production uses faster-whisper; tests inject a mock."""


@dataclass(frozen=True)
class TakeValidation:
    ok_format: bool
    duration_s: float | None
    similarity: float
    matches_script: bool
    transcript: str
    diff: tuple[str, ...]
    verdict: str  # accept | offer_accept_as_performed | request_rerecord | reject_format


def _script_similarity(script: str, transcript: str) -> tuple[float, tuple[str, ...]]:
    a = tokenize_words(script.lower())
    b = tokenize_words(transcript.lower())
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ratio = matcher.ratio()
    diff: list[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        if op in {"replace", "delete"} and i2 > i1:
            diff.append(f"- {' '.join(a[i1:i2])}")
        if op in {"replace", "insert"} and j2 > j1:
            diff.append(f"+ {' '.join(b[j1:j2])}")
    return ratio, tuple(diff)


def validate_take(
    wav_path: Path,
    script: str,
    *,
    transcriber: Transcriber,
    tolerance: float = 0.85,
    min_duration_s: float = 1.0,
    max_duration_s: float = 600.0,
    target_duration_s: tuple[float, float] | None = None,
) -> TakeValidation:
    try:
        with wave.open(str(wav_path)) as wf:
            duration = wf.getnframes() / wf.getframerate()
    except (wave.Error, FileNotFoundError, EOFError):
        return TakeValidation(False, None, 0.0, False, "", (), "reject_format")
    if not (min_duration_s <= duration <= max_duration_s):
        return TakeValidation(
            False,
            duration,
            0.0,
            False,
            "",
            (f"duration {duration:.1f}s outside {min_duration_s}-{max_duration_s}s",),
            "reject_format",
        )
    transcript = transcriber(wav_path)
    similarity, diff = _script_similarity(script, transcript)
    matches = similarity >= tolerance
    verdict = (
        "accept"
        if similarity >= 0.98
        else ("offer_accept_as_performed" if matches else "request_rerecord")
    )
    if target_duration_s and not (target_duration_s[0] <= duration <= target_duration_s[1]):
        diff = (
            *diff,
            f"duration {duration:.1f}s outside the direction {target_duration_s[0]:.0f}-{target_duration_s[1]:.0f}s",  # noqa: E501
        )
        if verdict == "accept":
            verdict = "offer_accept_as_performed"
    return TakeValidation(True, duration, round(similarity, 4), matches, transcript, diff, verdict)
