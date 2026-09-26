"""Human contribution and narration: recording brief, two-stage alignment, mixed take assembly."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from content_factory.audio.continuity import match_levels
from content_factory.audio.normalize import NUMBER_TOKEN, fold_spelling, is_number_word
from content_factory.audio.takes import faster_whisper_words
from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.timing import Pauses, take_cuts
from content_factory.explainer.units import UNIT_WORDS
from content_factory.human_tasks.validation import validate_take
from content_factory.schemas.base import canonical_dumps
from content_factory.schemas.explainer import (
    AlignedWord,
    Alignment,
    EvidencePack,
    NarrationManifest,
    NarrationTake,
    ScriptPlan,
    ScriptSegment,
    TokenRef,
    VoiceSpec,
)

REPO = Path(__file__).resolve().parents[3]
ALIGN_SKILL = REPO / "skills" / "audio" / "align"
ALIGN_MODEL_DIR = REPO / "models" / "speech" / "wav2vec2-base-960h"
BRIEF_WORDS_PER_S = 2.6
BRIEF_RANGE_S = (60, 90)
ACCEPT_SIMILARITY = 0.98
LOW_CONFIDENCE = 0.3
STEM_RATE_HZ = 48000
SEGMENT_GAP_MS = 250
SECTION_GAP_MS = 400

Asr = Callable[[Path], Sequence[str]]
Aligner = Callable[[Path, Sequence[str]], Mapping[str, Any]]
Synth = Callable[[ScriptSegment, VoiceSpec, Path], None]
PassageRole = Literal["hook", "observation", "conclusion"]
DeviationKind = Literal["insert", "delete", "replace"]
_KINDS: dict[str, DeviationKind] = {"insert": "insert", "delete": "delete", "replace": "replace"}


class NarrationError(RuntimeError):
    """A tool the narration stage runs (ASR, aligner, ffmpeg) failed or returned nonsense."""


# --- recording brief ---


@dataclass(frozen=True)
class BriefPassage:
    role: PassageRole
    segment_ids: tuple[str, ...]
    spoken_text: str
    claims: tuple[str, ...]
    word_count: int
    seconds: float


@dataclass(frozen=True)
class RecordingBrief:
    """What the creator reads in their own voice: hook, one observation, conclusion, a memo."""

    script_id: str
    question: str
    passages: tuple[BriefPassage, ...]
    memo_prompt: str | None
    total_words: int
    total_seconds: float
    in_hypothesis_range: bool


def recording_brief(script: ScriptPlan, pack: EvidencePack, *, memo: bool = True) -> RecordingBrief:
    consequential = {c.claim_id for c in pack.claims if c.consequential}
    hook = [s for s in script.segments if s.section == "cold_open"]
    conclusion = [s for s in script.segments if s.section == "synthesis"]
    taken = {s.segment_id for s in (*hook, *conclusion)}
    middle = [s for s in script.segments if s.segment_id not in taken]
    observation = max(
        middle, key=lambda s: len(consequential.intersection(s.claim_ids)), default=None
    )
    if observation is not None and not consequential.intersection(observation.claim_ids):
        observation = next(
            (s for s in middle if s.section in {"run_system", "change_variable"}), None
        )
    chosen: tuple[tuple[PassageRole, list[ScriptSegment]], ...] = (
        ("hook", hook),
        ("observation", [observation] if observation else []),
        ("conclusion", conclusion),
    )
    passages = [_passage(role, segments, pack) for role, segments in chosen if segments]
    words = sum(p.word_count for p in passages)
    seconds = round(words / BRIEF_WORDS_PER_S, 1)
    return RecordingBrief(
        script_id=script.script_id,
        question=script.question,
        passages=tuple(passages),
        memo_prompt=(
            f"Unscripted, up to a minute, in your own words: {script.contribution}"
            if memo
            else None
        ),
        total_words=words,
        total_seconds=seconds,
        in_hypothesis_range=BRIEF_RANGE_S[0] <= seconds <= BRIEF_RANGE_S[1],
    )


def brief_markdown(brief: RecordingBrief) -> str:
    lo, hi = BRIEF_RANGE_S
    verdict = "inside" if brief.in_hypothesis_range else "outside"
    lines = [
        f"# Recording brief: {brief.script_id}",
        "",
        f"**Question:** {brief.question}",
        "",
        "Read each passage in your own words; the wording below is the script's, not a prompt to "
        "recite. Numbers keep their unit and time basis.",
    ]
    for passage in brief.passages:
        lines += [
            "",
            f"## {passage.role.capitalize()} ({passage.word_count} words, about "
            f"{passage.seconds:.0f} s; segments {', '.join(passage.segment_ids)})",
            "",
            passage.spoken_text,
        ]
        if passage.claims:
            lines += ["", "Claims this passage makes:"]
            lines += [f"- {claim}" for claim in passage.claims]
    if brief.memo_prompt:
        lines += ["", "## Memo (optional, not counted below)", "", brief.memo_prompt]
    lines += [
        "",
        f"**Total:** {brief.total_words} words, about {brief.total_seconds:.0f} s at "
        f"{BRIEF_WORDS_PER_S} words/s, {verdict} the {lo}-{hi} s hypothesis range.",
        "",
    ]
    return "\n".join(lines)


def _passage(
    role: PassageRole, segments: Sequence[ScriptSegment], pack: EvidencePack
) -> BriefPassage:
    statements = {c.claim_id: c.statement for c in pack.claims}
    claims = tuple(statements[cid] for s in segments for cid in s.claim_ids if cid in statements)
    words = sum(len(s.tokens) for s in segments)
    return BriefPassage(
        role=role,
        segment_ids=tuple(s.segment_id for s in segments),
        spoken_text=" ".join(s.spoken_text for s in segments),
        claims=claims,
        word_count=words,
        seconds=round(words / BRIEF_WORDS_PER_S, 1),
    )


# --- spoken words ---

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = ((10**9, "billion"), (10**6, "million"), (1000, "thousand"))
_NUMBER = re.compile(r"^(\d[\d,]*)(\.\d+)?([^\d]*)$")
_SYMBOLS = {"%": " percent ", "&": " and ", "×": " times "}  # noqa: RUF001  typed "times"


def number_words(text: str) -> list[str]:
    """Digits as spoken English: cardinals, decimals digit by digit, years in two halves."""
    match = _NUMBER.match(text)
    if match is None:
        return []
    whole, fraction, _ = match.groups()
    n = int(whole.replace(",", ""))
    year_like = "," not in whole and fraction is None and len(whole) == 4
    if year_like and (1100 <= n <= 1999 or 2010 <= n <= 2099):
        rest = n % 100
        words = _int_words(n // 100)
        words += (
            ["hundred"] if rest == 0 else ["oh", _ONES[rest]] if rest < 10 else _int_words(rest)
        )
    else:
        words = _int_words(n)
    if fraction is not None:
        words += ["point", *(_ONES[int(d)] for d in fraction[1:])]
    return words


def _int_words(n: int) -> list[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [_ONES[n // 100], "hundred"] + (_int_words(n % 100) if n % 100 else [])
    for scale, name in _SCALES:
        if n >= scale:
            return _int_words(n // scale) + [name] + (_int_words(n % scale) if n % scale else [])
    return []


def _unit_words() -> dict[str, str]:
    """Unit symbol -> the one plural word that names it; '%' by hand because none is plural."""
    candidates: dict[str, list[str]] = {}
    for word, unit in UNIT_WORDS.items():
        if word != unit and re.fullmatch(r"[a-z]+s", word):
            candidates.setdefault(unit, []).append(word)
    return {unit: ws[0] for unit, ws in candidates.items() if len(ws) == 1} | {"%": "percent"}


UNIT_SPOKEN = _unit_words()


def spoken_words_for_token(token: str) -> list[str]:
    """One script token as the letters-only words a CTC aligner can spell; may be empty."""
    text = token
    for symbol, spoken in _SYMBOLS.items():
        text = text.replace(symbol, spoken)
    words: list[str] = []
    for part in re.split(r"[\s\-‐-―/]+", text):  # noqa: RUF001  every dash splits
        part = re.sub(r"^[^\w']+|[^\w']+$", "", part)
        match = _NUMBER.match(part)
        if match is not None:
            words += number_words(part)
            suffix = match.group(3)
            unit = UNIT_WORDS.get(suffix) or UNIT_WORDS.get(suffix.lower())
            if suffix:
                words.append(UNIT_SPOKEN.get(unit or "", suffix) if unit else suffix)
            continue
        letters = re.sub(r"[^A-Za-z']", "", part)
        if letters.strip("'"):
            words.append(letters)
    return words


def spoken_words_for_alignment(tokens: Sequence[str]) -> list[str]:
    """Every token's spoken words, flattened, in order."""
    return [word for token in tokens for word in spoken_words_for_token(token)]


# --- stage 1: verify ---


@dataclass(frozen=True)
class Deviation:
    """One place the performed words left the script; position is the script token index."""

    kind: DeviationKind
    script_words: tuple[str, ...]
    spoken_words: tuple[str, ...]
    position: int

    def __str__(self) -> str:
        script = " ".join(self.script_words) or "nothing"
        spoken = " ".join(self.spoken_words) or "nothing"
        return f"{self.kind} at token {self.position}: script {script!r}, heard {spoken!r}"


@dataclass(frozen=True)
class TakeVerification:
    similarity: float
    deviations: tuple[Deviation, ...]
    accepted: bool
    heard: tuple[str, ...]


def faster_whisper_asr(audio: Path, *, timeout_s: int = 600) -> list[str]:
    """Words heard by faster-whisper base.en on the CPU, the same call audio/takes.py makes."""
    words, _spans = faster_whisper_words(
        audio, model="base.en", compute_type="int8", timeout_s=timeout_s
    )
    return words


def verify_take(audio: Path, segment: ScriptSegment, *, asr: Asr | None = None) -> TakeVerification:
    """Stage 1: what was said against what the script says, every difference named."""
    heard = tuple(str(w) for w in (asr or faster_whisper_asr)(audio))
    review = validate_take(
        audio, segment.spoken_text, transcriber=lambda _p: " ".join(heard), min_duration_s=0.3
    )
    script_shape, script_at = _shape(segment.tokens)
    heard_shape, heard_at = _shape(heard)
    matcher = difflib.SequenceMatcher(a=script_shape, b=heard_shape, autojunk=False)
    deviations: list[Deviation] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        position = script_at[i1] if i1 < len(script_at) else len(segment.tokens)
        deviations.append(
            Deviation(
                kind=_KINDS[op],
                script_words=_covering(segment.tokens, script_at[i1:i2]),
                spoken_words=_covering(heard, heard_at[j1:j2]),
                position=position,
            )
        )
    return TakeVerification(
        similarity=review.similarity,
        deviations=tuple(deviations),
        accepted=review.ok_format and review.similarity >= ACCEPT_SIMILARITY,
        heard=heard,
    )


def _shape(words: Sequence[str]) -> tuple[list[str], list[int]]:
    """spoken_word_shape over separate words, keeping which word each shape entry came from."""
    shape: list[str] = []
    origin: list[int] = []
    in_run = False
    for index, word in enumerate(words):
        parts = [p for p in re.sub(r"^[^\w]+|[^\w]+$", "", word.lower()).split("-") if p]
        for part in parts:
            if is_number_word(part):
                if not in_run:
                    shape.append(NUMBER_TOKEN)
                    origin.append(index)
                    in_run = True
                continue
            in_run = False
            shape.append(fold_spelling(part))
            origin.append(index)
    return shape, origin


def _covering(words: Sequence[str], indexes: Sequence[int]) -> tuple[str, ...]:
    return tuple(words[i] for i in sorted(set(indexes)))


# --- stage 2: align ---


def run_align_skill(
    audio: Path,
    words: Sequence[str],
    *,
    model_dir: Path = ALIGN_MODEL_DIR,
    timeout_s: int = 900,
) -> dict[str, Any]:
    """The align skill in its own environment; the control plane never imports torch."""
    uv = shutil.which("uv")
    if uv is None:
        msg = "uv is not on PATH; run ./setup.sh"
        raise NarrationError(msg)
    with tempfile.TemporaryDirectory(prefix="cf-align-") as tmp:
        out = Path(tmp) / "align.json"
        proc = subprocess.run(  # noqa: S603  argument array, no shell, paths are ours
            [
                uv,
                "run",
                "--project",
                str(ALIGN_SKILL),
                "python",
                str(ALIGN_SKILL / "run.py"),
                "--audio",
                str(audio),
                "--words",
                json.dumps(list(words)),
                "--model",
                str(model_dir),
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            msg = f"align skill failed on {audio.name}: {proc.stderr.strip()[-600:]}"
            raise NarrationError(msg)
        return json.loads(out.read_text(encoding="utf-8"))


def align_take(
    audio: Path, segments: Sequence[ScriptSegment], *, aligner: Aligner | None = None
) -> Alignment:
    """Stage 2: word times for verified segments; tokens the aligner cannot place stay explicit."""
    refs: list[tuple[str, int]] = []
    words: list[str] = []
    unmatched: list[TokenRef] = []
    for segment in segments:
        for k, token in enumerate(segment.tokens):
            spoken = spoken_words_for_token(token)
            if not spoken:
                unmatched.append(TokenRef(segment_id=segment.segment_id, token_index=k))
            refs += [(segment.segment_id, k)] * len(spoken)
            words += spoken
    result = (aligner or run_align_skill)(audio, words)
    _, _, duration_ms = _wav_facts(audio)
    placed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for entry in result.get("words", ()):
        placed.setdefault(refs[int(entry["index"])], []).append(entry)
    aligned: list[AlignedWord] = []
    low: list[TokenRef] = []
    for ref in dict.fromkeys(refs):
        entries = placed.get(ref)
        segment_id, k = ref
        if not entries:
            unmatched.append(TokenRef(segment_id=segment_id, token_index=k))
            continue
        confidence = min(1.0, max(0.0, sum(float(e["score"]) for e in entries) / len(entries)))
        start = min(int(e["start_ms"]) for e in entries)
        end = min(duration_ms, max(int(e["end_ms"]) for e in entries))
        aligned.append(
            AlignedWord(
                segment_id=segment_id,
                token_index=k,
                start_ms=min(start, end),
                end_ms=end,
                confidence=confidence,
            )
        )
        if confidence < LOW_CONFIDENCE:
            low.append(TokenRef(segment_id=segment_id, token_index=k))
    return Alignment(
        aligner=str(result.get("aligner", "torchaudio.forced_align")),
        aligner_version=str(result.get("aligner_version", "unknown")),
        model_id=str(result.get("model_id", "")),
        words=tuple(aligned),
        unmatched=tuple(unmatched),
        low_confidence=tuple(low),
    )


# --- assembly ---


def synth_cache_key(segment: ScriptSegment, voice: VoiceSpec, *, seed: int = 0) -> str:
    """Gate E2: what the voice says and who says it; display_text is not in the key."""
    payload = {
        "spoken_text": segment.spoken_text,
        "voice": {
            "kind": voice.kind,
            "voice_id": voice.voice_id,
            "model_id": voice.model_id,
            "model_revision": voice.model_revision,
        },
        "seed": seed,
    }
    return hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()


def assemble_narration(
    script: ScriptPlan,
    takes: Mapping[str, Path],
    *,
    synth: Synth,
    voice: VoiceSpec,
    out_dir: Path,
    seed: int = 0,
    asr: Asr | None = None,
    aligner: Aligner | None = None,
    created_at: str | None = None,
) -> tuple[NarrationManifest, Path]:
    """One take per segment, recorded where given and synthesized otherwise, as one 48 kHz stem."""
    out_dir.mkdir(parents=True, exist_ok=True)
    issues: list[ContractIssue] = []
    chosen: list[tuple[ScriptSegment, Path, Literal["recorded", "synthesized"], float | None]] = []
    for i, segment in enumerate(script.segments):
        recorded = takes.get(segment.segment_id)
        if recorded is not None:
            verification = verify_take(recorded, segment, asr=asr)
            if not verification.accepted:
                issues.append(_mismatch(i, segment, recorded, verification))
                continue
            chosen.append((segment, recorded, "recorded", verification.similarity))
            continue
        path = out_dir / "cache" / f"{synth_cache_key(segment, voice, seed=seed)}.wav"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            synth(segment, voice, path)
        # None means not measured: a synthesized take is only verified when an ASR is supplied.
        similarity = verify_take(path, segment, asr=asr).similarity if asr is not None else None
        chosen.append((segment, path, "synthesized", similarity))
    if issues:
        raise EpisodeInvalidError(issues)
    clips = [read_wav_48k(path) for _, path, _, _ in chosen]
    # match_levels measures speech only (pauses dropped) and caps at 3 dB: a long line is not quiet.
    clips = match_levels(clips, STEM_RATE_HZ)
    stem_parts: list[np.ndarray] = []
    manifest_takes: list[NarrationTake] = []
    cursor = 0
    previous_section: str | None = None
    for (segment, path, kind, similarity), clip in zip(chosen, clips, strict=True):
        if previous_section is not None:
            gap = SEGMENT_GAP_MS if segment.section == previous_section else SECTION_GAP_MS
            stem_parts.append(np.zeros(gap * STEM_RATE_HZ // 1000, dtype=np.float32))
            cursor += gap
        rate, _, duration_ms = _wav_facts(path)
        samples = duration_ms * STEM_RATE_HZ // 1000
        stem_parts.append(np.pad(clip[:samples], (0, max(0, samples - len(clip)))))
        audio_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_takes.append(
            NarrationTake(
                take_id=take_id_for(segment.segment_id, audio_sha),
                segment_ids=(segment.segment_id,),
                kind=kind,
                audio_sha256=audio_sha,
                start_ms=cursor,
                duration_ms=duration_ms,
                sample_rate_hz=rate,
                transcript_similarity=None if similarity is None else round(similarity, 4),
                alignment=align_take(path, [segment], aligner=aligner),
            )
        )
        cursor += duration_ms
        previous_section = segment.section
    stem = out_dir / "narration-stem.wav"
    write_wav(stem, np.concatenate(stem_parts), STEM_RATE_HZ)
    stem_sha = hashlib.sha256(stem.read_bytes()).hexdigest()
    manifest = NarrationManifest(
        manifest_id=f"nar_{stem_sha[:12]}",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        voice=voice,
        takes=tuple(manifest_takes),
        stem_sha256=stem_sha,
        total_duration_ms=cursor,
        created_at=created_at or datetime.now(UTC).isoformat(timespec="seconds"),
    )
    (out_dir / "narration-manifest.json").write_text(
        manifest.canonical_json() + "\n", encoding="utf-8"
    )
    return manifest, stem


def pace_stem(
    stem: Path, narration: NarrationManifest, paced: NarrationManifest, pauses: Pauses, out: Path
) -> str:
    """The stem re-laid at the paced starts, silence cut in mid-take; returns its sha256."""
    audio = read_wav_48k(stem)
    laid = np.zeros(_samples(paced.total_duration_ms), dtype=np.float32)
    starts = {t.take_id: t.start_ms for t in paced.takes}
    for take in narration.takes:
        begin, added = 0, 0
        for cut, ms in [*take_cuts(take, pauses), (take.duration_ms, 0)]:
            piece = audio[_samples(take.start_ms + begin) : _samples(take.start_ms + cut)]
            at = _samples(starts[take.take_id] + begin + added)
            laid[at : at + len(piece)] = piece[: max(0, len(laid) - at)]
            begin, added = cut, added + ms
    write_wav(out, laid, STEM_RATE_HZ)
    return hashlib.sha256(out.read_bytes()).hexdigest()


def _samples(ms: int) -> int:
    return ms * STEM_RATE_HZ // 1000


def take_id_for(segment_id: str, audio_sha256: str) -> str:
    """The NarrationTake id of one segment spoken by one exact recording."""
    return "take_" + hashlib.sha256(f"{segment_id}:{audio_sha256}".encode()).hexdigest()[:16]


def _mismatch(
    index: int, segment: ScriptSegment, audio: Path, verification: TakeVerification
) -> ContractIssue:
    listed = "; ".join(str(d) for d in verification.deviations) or "no words heard"
    return ContractIssue(
        kind="narration_mismatch",
        where=f"NarrationManifest.takes[{index}]",
        message=(
            f"take {audio.name} for segment {segment.segment_id} matches the script at "
            f"{verification.similarity:.2f} (accept at {ACCEPT_SIMILARITY}): {listed}."
        ),
        fix=(
            f"re-record segment {segment.segment_id} or accept the performed wording by editing "
            "spoken_text."
        ),
        ids=(segment.segment_id, audio.name),
    )


def _wav_facts(path: Path) -> tuple[int, int, int]:
    """(sample_rate_hz, channels, duration_ms) of a PCM WAV."""
    with wave.open(str(path), "rb") as wf:
        frames, rate, channels = wf.getnframes(), wf.getframerate(), wf.getnchannels()
    if frames == 0 or rate == 0:
        msg = f"{path.name}: empty recording"
        raise NarrationError(msg)
    return rate, channels, max(1, round(frames * 1000 / rate))


def read_wav_48k(path: Path) -> np.ndarray:
    """A take as float32 mono at the stem rate; ffmpeg resamples so every take shares one clock."""
    from content_factory.audio.mix import ffmpeg

    rate, channels, _ = _wav_facts(path)
    if rate == STEM_RATE_HZ and channels == 1:
        return _read_wav(path)
    with tempfile.TemporaryDirectory(prefix="cf-take-") as tmp:
        converted = Path(tmp) / "take48k.wav"
        ffmpeg(
            [
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                str(STEM_RATE_HZ),
                "-c:a",
                "pcm_s16le",
                "-map_metadata",
                "-1",
                str(converted),
            ]
        )
        return _read_wav(converted)


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        channels, width = wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        msg = f"{path.name}: {width * 8}-bit PCM; the narration chain reads 16-bit WAV"
        raise NarrationError(msg)
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
