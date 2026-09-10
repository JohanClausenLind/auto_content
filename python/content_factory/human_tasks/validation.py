"""Human task submission validation (14.3): probes + ASR-vs-script diff with a configurable
tolerance. Improvisation within tolerance offers accept-as-performed; a mismatch shows the diff
and never silently accepts. Takes are immutable; a rejected take never deletes prior takes."""

from __future__ import annotations

import difflib
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from content_factory.audio.normalize import NUMBER_TOKEN, spoken_word_shape

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


NEAR_WORD_RATIO = 0.72
"""How alike two spellings of one word have to be to count as the same word heard.

Measured on this repo's own failing beats: an English aligner writes ``craftnet`` for
``kraftnät`` (0.750) and ``energemendite`` for ``Energimyndigheten`` (0.733) — it heard the word
and spelled it the only way its vocabulary allows. Below this, a replacement is a different word:
``svenska``/``energimyndigheten`` is 0.167 and ``always``/``never`` is 0.000.

The threshold does admit a pair like ``increase``/``decrease`` (0.750), and that is a deliberate
limit rather than an oversight: this gate exists to catch a model that **garbled or truncated** a
line, which shows up as words missing or as an unrelated token, not as an antonym one letter
different. A text-to-speech model does not swap a word for its opposite; an aligner mishearing a
long word is the failure that actually happens, and it happened on every beat naming a place.
"""


NEAR_RUN_RATIO = 0.70
"""How alike two *runs* have to be, joined, to count as one wording tokenized two ways.

Separate from :data:`NEAR_WORD_RATIO` because it answers a different question. An aligner writes
``some light`` for ``sunlight`` (0.706 joined) and ``per cent`` for ``percent``: one word became
two, so there is no pair to compare and the per-word rule cannot see it at all. Where the two
sides carry the same letters in the same order, where they are split is the aligner's convention
and not something the speaker did.
"""


def _near_words(script_run: list[str], heard_run: list[str]) -> list[tuple[str, str]]:
    """Pairs in a replaced run that are the same word spelled two ways, in order."""
    return [
        (a, b)
        for a, b in zip(script_run, heard_run, strict=False)
        if difflib.SequenceMatcher(a=a, b=b).ratio() >= NEAR_WORD_RATIO
    ]


def _same_run_resplit(script_run: list[str], heard_run: list[str]) -> bool:
    """Whether a replaced run is the same wording with the word boundaries in other places."""
    if len(script_run) == len(heard_run):
        return False  # a one-for-one swap is the per-word rule's business, not this one
    a, b = "".join(script_run), "".join(heard_run)
    return bool(a) and bool(b) and difflib.SequenceMatcher(a=a, b=b).ratio() >= NEAR_RUN_RATIO


def _script_similarity(script: str, transcript: str) -> tuple[float, tuple[str, ...]]:
    """How closely a transcript matches the script, with numbers compared as numbers.

    Every run of digits or number words on **both** sides collapses to one sentinel first. Two
    systems render a figure differently and neither is wrong: measured on this repo's own
    fixtures, a script saying "40.8 terawatt-hours" transcribed as "40 8 terawatt hours" scored
    0.73, and "twelve hundred"/"four fifty" came back as "1200"/"450" for the same 0.73. Both are
    correct reads, and comparing the renderings measured the aligner's formatting rather than
    whether the model said the script.

    A beat that **drops** a figure still fails: the sentinel is per run, so a missing number is a
    missing token in the sequence. What this no longer claims to check is *which* figure was
    spoken — see `spoken_word_shape` for why an ASR transcript is not evidence of that.
    """
    a, script_runs = spoken_word_shape(script)
    b, heard_runs = spoken_word_shape(transcript)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    diff: list[str] = []
    matched = 0
    respelled: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            matched += i2 - i1
            continue
        if op == "replace":
            script_run, heard_run = a[i1:i2], b[j1:j2]
            if _same_run_resplit(script_run, heard_run):
                # Every script word in the run was said; the aligner put the spaces elsewhere.
                respelled += [(" ".join(script_run), " ".join(heard_run))] * len(script_run)
            else:
                respelled += _near_words(script_run, heard_run)
        if op in {"replace", "delete"} and i2 > i1:
            diff.append(f"- {' '.join(a[i1:i2])}")
        if op in {"replace", "insert"} and j2 > j1:
            diff.append(f"+ {' '.join(b[j1:j2])}")
    # A word the aligner spelled its own way is a word it heard, so it counts as said. Without
    # this, one place name sank a whole beat: "Sources: Energimyndigheten, Svenska kraftnät."
    # scored 0.44 against a 0.80 gate on a read that was word-perfect, because an English ASR
    # cannot spell either name and this measured the spelling.
    ratio = (2 * (matched + len(respelled)) / (len(a) + len(b))) if (a or b) else 1.0
    if respelled:
        pairs = ", ".join(f"{x}/{y}" for x, y in respelled[:4])
        diff.append(f"~ {len(respelled)} word(s) heard, spelled differently: {pairs}")
    if script_runs != heard_runs:
        diff.append(f"~ {script_runs} figure(s) in the script, {heard_runs} heard")
    # The sentinel is unreadable in a diff a person reads, so it is spelled out there.
    readable = tuple(line.replace(NUMBER_TOKEN, "<number>") for line in diff)
    return ratio, readable


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
