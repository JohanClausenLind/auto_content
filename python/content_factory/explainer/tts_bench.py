"""TTS benchmark behind a hard licence gate: only commercially usable candidates ever synthesize."""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from content_factory.audio.takes import faster_whisper_words

REPO = Path(__file__).resolve().parents[3]
PASSAGES_PATH = REPO / "fixtures" / "explainer" / "tts_bench" / "passages.json"
QWEN_SKILL = REPO / "skills" / "audio" / "qwen3tts"
CHATTERBOX_SKILL = REPO / "skills" / "audio" / "chatterbox"
ASR_MODEL, ASR_COMPUTE = "base.en", "int8"
ASR_TIMEOUT_S = 1800
SYNTH_TIMEOUT_S = 3600
VRAM_FREE_TARGET_MIB = 18_000
VRAM_POLL_S = 0.5
VRAM_WAIT_TIMEOUT_S = 1800
OOM_MARKER = "out of memory"
LONG_FORM_TOLERANCE = 0.10
NO_REFERENCE = "no creator reference recording"
CLONE_NEEDS_REFERENCE = "voice cloning needs a creator reference recording (--ref-audio)"
SEED = 7
QWEN_SPEAKER = "ryan"
VOICE_DESIGN = (
    "A calm, clear adult male narrator with a neutral accent, a steady unhurried pace and a warm "
    "mid-range timbre."
)


class LicenseGateError(PermissionError):
    """A candidate whose licence forbids commercial output was asked to synthesize."""


class BenchmarkError(RuntimeError):
    """An executor, the ASR or the GPU tenancy check failed."""


@dataclass(frozen=True)
class SynthRequest:
    text: str
    out: Path
    seed: int
    ref_audio: Path | None = None
    ref_text: str | None = None


@dataclass(frozen=True)
class SynthResult:
    """What an executor reports; `synth_s` excludes model load, duration is re-read from disk."""

    wav: Path
    duration_s: float
    sample_rate: int
    synth_s: float
    mode: str


Runner = Callable[[SynthRequest], SynthResult]
Asr = Callable[[Path], Sequence[str]]
Similarity = Callable[[Path, Path], float]
Sampler = Callable[[], int | None]


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str
    license: str
    commercial_output_allowed: bool
    license_note: str
    runner: Runner | None
    needs_reference: bool = False


@dataclass(frozen=True)
class Passage:
    id: str
    kind: str
    text: str
    acronyms: tuple[str, ...] = ()
    long_form: bool = False


def selectable(candidate: Candidate) -> bool:
    """The gate: only a licence that allows commercial use of the output may be selected."""
    return candidate.commercial_output_allowed


# --- executors ---


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise BenchmarkError(f"executor printed no JSON line: {stdout[-400:]!r}")


def _run_executor(cmd: list[str], *, stdin: str | None = None) -> dict[str, Any]:
    proc = subprocess.run(  # noqa: S603  argument array, executors inside the repo
        cmd, input=stdin, capture_output=True, text=True, timeout=SYNTH_TIMEOUT_S, check=False
    )
    if proc.returncode != 0:
        name = next((Path(c).parent.name for c in cmd if c.endswith(".py")), cmd[0])
        raise BenchmarkError(f"{name} failed: {proc.stderr.strip()[-800:]}")
    return _last_json_line(proc.stdout)


def _skill_python(skill: Path) -> list[str]:
    return ["uv", "run", "--project", str(skill), "python", str(skill / "run.py")]


def _qwen_runner(mode: str) -> Runner:
    def run(req: SynthRequest) -> SynthResult:
        cmd = [
            *_skill_python(QWEN_SKILL),
            "--language",
            "English",
            "--device",
            "cuda:0",
            "--out",
            str(req.out),
            "--seed",
            str(req.seed),
        ]
        if mode == "custom_voice":
            cmd += ["--speaker", QWEN_SPEAKER]
        elif mode == "voice_design":
            cmd += ["--describe", VOICE_DESIGN]
        else:
            if req.ref_audio is None:
                raise BenchmarkError(CLONE_NEEDS_REFERENCE)
            cmd += ["--ref-audio", str(req.ref_audio)]
            cmd += ["--ref-text", req.ref_text] if req.ref_text else ["--x-vector-only"]
        out = _run_executor(cmd, stdin=req.text)
        return SynthResult(
            wav=Path(out["wav"]),
            duration_s=float(out["duration_ms"]) / 1000,
            sample_rate=int(out["sample_rate"]),
            synth_s=float(out.get("generate_s", 0.0)),
            mode=str(out.get("mode", mode)),
        )

    return run


def _chatterbox_runner(req: SynthRequest) -> SynthResult:
    cmd = [
        *_skill_python(CHATTERBOX_SKILL),
        "--text",
        req.text,
        "--out",
        str(req.out),
        "--seed",
        str(req.seed),
    ]
    if req.ref_audio is not None:
        cmd += ["--ref-audio", str(req.ref_audio)]
    out = _run_executor(cmd)
    return SynthResult(
        wav=Path(out["wav"]),
        duration_s=float(out["duration_s"]),
        sample_rate=int(out["sample_rate"]),
        synth_s=float(out["wall_s"]),
        mode="clone" if req.ref_audio is not None else "builtin_voice",
    )


def _speaker_similarity(ref: Path, wav: Path) -> float:
    cmd = [
        "uv",
        "run",
        "--project",
        str(CHATTERBOX_SKILL),
        "python",
        str(CHATTERBOX_SKILL / "speaker_sim.py"),
        "--ref",
        str(ref),
        "--wav",
        str(wav),
    ]
    return float(_run_executor(cmd)["cosine"])


def _asr_words(wav: Path) -> Sequence[str]:
    words, _spans = faster_whisper_words(
        wav, model=ASR_MODEL, compute_type=ASR_COMPUTE, timeout_s=ASR_TIMEOUT_S, language="en"
    )
    return words


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        key="qwen3-tts-base",
        label="Qwen3-TTS 12Hz 1.7B Base (voice clone)",
        license="Apache-2.0",
        commercial_output_allowed=True,
        license_note="Apache-2.0 code and weights; clones the creator's reference recording.",
        runner=_qwen_runner("base"),
        needs_reference=True,
    ),
    Candidate(
        key="qwen3-tts-customvoice",
        label=f"Qwen3-TTS 12Hz 1.7B CustomVoice ({QWEN_SPEAKER})",
        license="Apache-2.0",
        commercial_output_allowed=True,
        license_note="Apache-2.0 code and weights; preset timbre.",
        runner=_qwen_runner("custom_voice"),
    ),
    Candidate(
        key="qwen3-tts-voicedesign",
        label="Qwen3-TTS 12Hz 1.7B VoiceDesign",
        license="Apache-2.0",
        commercial_output_allowed=True,
        license_note="Apache-2.0 code and weights; one fixed voice description.",
        runner=_qwen_runner("voice_design"),
    ),
    Candidate(
        key="chatterbox-turbo",
        label="Chatterbox Turbo 350M",
        license="MIT",
        commercial_output_allowed=True,
        license_note="MIT code and weights; every output carries the Resemble Perth watermark.",
        runner=_chatterbox_runner,
    ),
    Candidate(
        key="voxtral-tts",
        label="Voxtral 4B TTS 2603",
        license="CC-BY-NC-4.0",
        commercial_output_allowed=False,
        license_note="CC-BY-NC-4.0 weights: no commercial use of the model or its output.",
        runner=None,
    ),
    Candidate(
        key="breeze-tts2",
        label="Breeze TTS 2 (3B)",
        license="BreezeBlue Research and Non-Commercial License",
        commercial_output_allowed=False,
        license_note="Weights and self-hosted outputs are research and non-commercial only.",
        runner=None,
    ),
    Candidate(
        key="fish-s2-pro",
        label="Fish Audio S2 Pro (5B)",
        license="Fish Audio Research License",
        commercial_output_allowed=False,
        license_note="No commercial rights without a paid agreement.",
        runner=None,
    ),
)
CANDIDATE_BY_KEY = {c.key: c for c in CANDIDATES}


# --- transcript scoring ---

_UNITS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_NUMBER_WORDS = frozenset(_UNITS) | frozenset(_TENS) | frozenset(_SCALES) | {"point"}
_ALIASES = {"jason": "json"}
_DECIMAL_MARK = "\x00"
_BOUNDARY = "\x01"
_NUMBER_TOKEN = re.compile(r"^\d+(\.\d+)?$")


def normalize_words(text: str) -> list[str]:
    """Words as a scorer compares them: lower case, no punctuation, every number in digits."""
    t = re.sub(r"(\d)\s+([.,])\s*(\d)", r"\1\2\3", text.lower())
    t = t.replace("%", " percent ")
    t = re.sub(r"(\d),(?=\d{3}(?!\d))", r"\1", t)
    t = re.sub(r"(?<=\d)\.(?=\d)", _DECIMAL_MARK, t)
    # Punctuation becomes a boundary token so "twenty, one turn" never reads as 21; dropped after.
    t = re.sub(r"[^\w\s\x00]", f" {_BOUNDARY} ", t).replace("_", " ")
    tokens = t.replace(_DECIMAL_MARK, ".").split()
    tokens = _join_spelled_acronyms(_numbers_to_digits(tokens))
    return [_ALIASES.get(w, w) for w in tokens if w != _BOUNDARY]


def _join_spelled_acronyms(tokens: list[str]) -> list[str]:
    """Letter-by-letter ASR spellings rejoin: g p u becomes gpu, q 3 becomes q3."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        j = i
        while j < len(tokens) and len(tokens[j]) == 1 and tokens[j].isalpha():
            j += 1
        if j - i >= 2:
            out.append("".join(tokens[i:j]))
            i = j
            continue
        if j - i == 1 and tokens[i] != "a" and j < len(tokens) and tokens[j].isdigit():
            out.append(tokens[i] + tokens[j])
            i = j + 1
            continue
        out.append(tokens[i])
        i += 1
    return out


def _numbers_to_digits(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] not in _NUMBER_WORDS or tokens[i] == "point":
            out.append(tokens[i])
            i += 1
            continue
        j = i
        while j < len(tokens) and (tokens[j] in _NUMBER_WORDS or _bridging_and(tokens, j)):
            j += 1
        run = [w for w in tokens[i:j] if w != "and"]
        while run and run[-1] == "point":
            run.pop()
            j -= 1
        out.append(_run_to_digits(run))
        i = j
    return out


def _bridging_and(tokens: list[str], j: int) -> bool:
    """An "and" after a scale word joins (two hundred and fifty); seventy and ninety stay two."""
    return (
        tokens[j] == "and"
        and tokens[j - 1] in _SCALES
        and j + 1 < len(tokens)
        and tokens[j + 1] in _NUMBER_WORDS
    )


def _run_to_digits(run: list[str]) -> str:
    if "point" in run:
        cut = run.index("point")
        whole = _integer_run(run[:cut]) if cut else "0"
        return whole + "." + "".join(str(_UNITS.get(w, 0)) for w in run[cut + 1 :])
    return _integer_run(run)


def _integer_run(words: list[str]) -> str:
    """Spoken integers to digits; a chunk that cannot extend the last one is read year-style."""
    parts: list[str] = []
    total = current = 0
    for w in words:
        if w in _SCALES:
            if w == "hundred":
                current = (current or 1) * 100
            else:
                total += (current or 1) * _SCALES[w]
                current = 0
            continue
        value = _UNITS.get(w, _TENS.get(w, 0))
        if current and not _extends(current, value):
            parts.append(str(total + current))
            total = current = 0
        current += value
    return "".join(parts) + str(total + current)


def _extends(current: int, value: int) -> bool:
    if current % 100 == 0:
        return True
    return 0 < value < 10 and current % 100 >= 20 and current % 10 == 0


def word_edit_counts(ref: Sequence[str], hyp: Sequence[str]) -> tuple[int, int, int]:
    """Substitutions, deletions and insertions of a word-level Levenshtein alignment."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i
    for j in range(1, m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(d[i - 1][j - 1] + cost, d[i - 1][j] + 1, d[i][j - 1] + 1)
    subs = dels = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        cost = 1 if i == 0 or j == 0 or ref[i - 1] != hyp[j - 1] else 0
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + cost:
            subs += cost
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return subs, dels, ins


@dataclass(frozen=True)
class TranscriptScore:
    wer: float
    substitutions: int
    omissions: int
    insertions: int
    numbers_ok: bool | None
    acronyms_ok: bool | None
    transcript_ratio: float
    long_form_completed: bool | None


def score_transcript(passage: Passage, hyp_words: Sequence[str]) -> TranscriptScore:
    ref = normalize_words(passage.text)
    hyp = normalize_words(" ".join(hyp_words))
    subs, dels, ins = word_edit_counts(ref, hyp)
    numbers = [w for w in ref if _NUMBER_TOKEN.match(w)]
    acronyms = [a for w in passage.acronyms for a in normalize_words(w)]
    ratio = len(hyp) / len(ref) if ref else 0.0
    return TranscriptScore(
        wer=round((subs + dels + ins) / max(len(ref), 1), 4),
        substitutions=subs,
        omissions=dels,
        insertions=ins,
        numbers_ok=_contains_all(hyp, numbers) if numbers else None,
        acronyms_ok=_contains_all(hyp, acronyms) if acronyms else None,
        transcript_ratio=round(ratio, 4),
        long_form_completed=abs(1 - ratio) <= LONG_FORM_TOLERANCE if passage.long_form else None,
    )


def _contains_all(hyp: Sequence[str], wanted: Sequence[str]) -> bool:
    pool = list(hyp)
    for w in wanted:
        if w not in pool:
            return False
        pool.remove(w)
    return True


# --- GPU tenancy and VRAM ---


def _nvidia_smi(query: str, field_: str) -> list[str] | None:
    try:
        out = subprocess.check_output(  # noqa: S603  fixed argument array
            ["nvidia-smi", f"--query-{query}={field_}", "--format=csv,noheader,nounits"],  # noqa: S607  PATH, as services/local.py
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def _first_int(lines: list[str] | None) -> int | None:
    if not lines:
        return None
    try:
        return int(float(lines[0].split(",")[0]))
    except ValueError:
        return None


def query_vram_used_mib() -> int | None:
    return _first_int(_nvidia_smi("gpu", "memory.used"))


def query_vram_free_mib() -> int | None:
    return _first_int(_nvidia_smi("gpu", "memory.free"))


def gpu_tenants() -> list[str]:
    """Compute processes on the card right now, so a report says who else held it."""
    return _nvidia_smi("compute-apps", "pid,process_name,used_memory") or []


def gpu_name() -> str:
    lines = _nvidia_smi("gpu", "name,driver_version")
    return lines[0] if lines else "no nvidia-smi"


def wait_for_gpu(
    free_mib: Sampler,
    *,
    target_mib: int = VRAM_FREE_TARGET_MIB,
    timeout_s: float = VRAM_WAIT_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int | None:
    """Block until the card has `target_mib` free; another tenant never shares it with a model."""
    deadline = clock() + timeout_s
    while True:
        free = free_mib()
        if free is None or free >= target_mib:
            return free
        if clock() >= deadline:
            raise BenchmarkError(
                f"only {free} MiB of VRAM free after {timeout_s:.0f} s; not starting"
            )
        sleep(VRAM_POLL_S * 4)


class VramMonitor:
    """Samples used VRAM on a thread during one synthesis; peak is reported over the baseline."""

    def __init__(self, sample: Sampler, interval_s: float = VRAM_POLL_S) -> None:
        self._sample = sample
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self.baseline_mib: int | None = None
        self.samples: list[int] = []

    def _poll(self) -> None:
        while not self._stop.wait(self._interval_s):
            value = self._sample()
            if value is not None:
                self.samples.append(value)

    def __enter__(self) -> VramMonitor:
        self.baseline_mib = self._sample()
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        value = self._sample()
        if value is not None:
            self.samples.append(value)

    @property
    def peak_mib(self) -> int | None:
        if self.baseline_mib is None or not self.samples:
            return None
        return max(0, max(self.samples) - self.baseline_mib)


# --- the run ---


@dataclass(frozen=True)
class BenchmarkRow:
    candidate: str
    passage: str
    mode: str
    wav: str
    duration_s: float
    wall_s: float
    process_s: float
    rtf: float
    peak_vram_mib: int | None
    sample_rate: int
    wer: float
    substitutions: int
    omissions: int
    insertions: int
    numbers_ok: bool | None
    acronyms_ok: bool | None
    transcript_ratio: float
    long_form_completed: bool | None
    speaker_similarity: float | None
    speaker_similarity_skipped: str | None
    transcript: str


@dataclass(frozen=True)
class Skipped:
    candidate: str
    passage: str
    reason: str


@dataclass(frozen=True)
class Exclusion:
    key: str
    label: str
    license: str
    reason: str


@dataclass
class BenchmarkReport:
    created_at: str
    host: str
    gpu: str
    other_tenants: list[str]
    ref_audio: str | None
    asr: str
    seed: int
    candidates_run: list[str]
    rows: list[BenchmarkRow] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    exclusions: list[Exclusion] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"


def load_passages(path: Path = PASSAGES_PATH) -> list[Passage]:
    data = json.loads(path.read_text())
    return [
        Passage(
            id=str(p["id"]),
            kind=str(p["kind"]),
            text=str(p["text"]),
            acronyms=tuple(str(a) for a in p.get("acronyms", ())),
            long_form=bool(p.get("long_form", False)),
        )
        for p in data["passages"]
    ]


def exclusions(candidates: Sequence[Candidate] = CANDIDATES) -> list[Exclusion]:
    return [
        Exclusion(key=c.key, label=c.label, license=c.license, reason=c.license_note)
        for c in candidates
        if not selectable(c)
    ]


def wav_facts(path: Path) -> tuple[float, int]:
    """Duration and sample rate read from the file, not trusted from the executor."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        return w.getnframes() / rate, rate


def _refuse(candidate: Candidate) -> None:
    if not selectable(candidate):
        raise LicenseGateError(
            f"{candidate.key} is excluded: licence {candidate.license!r} — {candidate.license_note}"
        )


def run_benchmark(
    candidates: Sequence[Candidate],
    passages: Sequence[Passage],
    out_dir: Path,
    *,
    ref_audio: Path | None = None,
    ref_text: str | None = None,
    seed: int = SEED,
    asr: Asr = _asr_words,
    vram_used_mib: Sampler = query_vram_used_mib,
    vram_free_mib: Sampler = query_vram_free_mib,
    similarity: Similarity = _speaker_similarity,
    wait_timeout_s: float = VRAM_WAIT_TIMEOUT_S,
    clock: Callable[[], float] = time.perf_counter,
    log: Callable[[str], object] = lambda _msg: None,
    checkpoint: Callable[[BenchmarkReport], object] = lambda _report: None,
) -> BenchmarkReport:
    """One model at a time: wait for the card, synthesize, transcribe, score, release."""
    for candidate in candidates:
        _refuse(candidate)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = BenchmarkReport(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        host=platform.node(),
        gpu=gpu_name(),
        other_tenants=gpu_tenants(),
        ref_audio=str(ref_audio) if ref_audio else None,
        asr=f"faster-whisper {ASR_MODEL} {ASR_COMPUTE} cpu",
        seed=seed,
        candidates_run=[c.key for c in candidates],
        exclusions=exclusions(),
    )
    for candidate in candidates:
        _refuse(candidate)
        if candidate.runner is None:
            report.skipped.append(Skipped(candidate.key, "*", "no executor"))
            continue
        if candidate.needs_reference and ref_audio is None:
            report.skipped.append(Skipped(candidate.key, "*", CLONE_NEEDS_REFERENCE))
            continue
        for passage in passages:
            row = _bench_with_retry(
                candidate,
                passage,
                out_dir,
                report,
                ref_audio=ref_audio,
                ref_text=ref_text,
                seed=seed,
                asr=asr,
                vram_used_mib=vram_used_mib,
                vram_free_mib=vram_free_mib,
                similarity=similarity,
                wait_timeout_s=wait_timeout_s,
                clock=clock,
                log=log,
            )
            if row is not None:
                report.rows.append(row)
            checkpoint(report)
    return report


def _bench_with_retry(
    candidate: Candidate,
    passage: Passage,
    out_dir: Path,
    report: BenchmarkReport,
    *,
    ref_audio: Path | None,
    ref_text: str | None,
    seed: int,
    asr: Asr,
    vram_used_mib: Sampler,
    vram_free_mib: Sampler,
    similarity: Similarity,
    wait_timeout_s: float,
    clock: Callable[[], float],
    log: Callable[[str], object],
) -> BenchmarkRow | None:
    """A CUDA OOM means another tenant took the card mid-run: wait for it and try once more."""
    for attempt in (1, 2):
        wait_for_gpu(vram_free_mib, timeout_s=wait_timeout_s)
        log(f"{candidate.key} / {passage.id}: synthesizing (attempt {attempt})")
        try:
            row = _bench_one(
                candidate,
                passage,
                out_dir,
                ref_audio=ref_audio,
                ref_text=ref_text,
                seed=seed,
                asr=asr,
                vram_used_mib=vram_used_mib,
                similarity=similarity,
                clock=clock,
            )
        except BenchmarkError as exc:
            reason = str(exc)
            if OOM_MARKER in reason.lower() and attempt == 1:
                log(f"{candidate.key} / {passage.id}: CUDA out of memory; retrying")
                continue
            report.skipped.append(Skipped(candidate.key, passage.id, reason[:600]))
            log(f"{candidate.key} / {passage.id}: failed: {reason[-200:]}")
            return None
        log(f"{candidate.key} / {passage.id}: wer {row.wer}")
        return row
    return None


def _bench_one(
    candidate: Candidate,
    passage: Passage,
    out_dir: Path,
    *,
    ref_audio: Path | None,
    ref_text: str | None,
    seed: int,
    asr: Asr,
    vram_used_mib: Sampler,
    similarity: Similarity,
    clock: Callable[[], float],
) -> BenchmarkRow:
    _refuse(candidate)
    assert candidate.runner is not None
    wav = out_dir / f"{candidate.key}__{passage.id}.wav"
    request = SynthRequest(passage.text, wav, seed, ref_audio=ref_audio, ref_text=ref_text)
    with VramMonitor(vram_used_mib) as monitor:
        t0 = clock()
        result = candidate.runner(request)
        process_s = clock() - t0
    duration_s, sample_rate = wav_facts(result.wav)
    wall_s = result.synth_s or process_s
    words = asr(result.wav)
    score = score_transcript(passage, words)
    cosine = similarity(ref_audio, result.wav) if ref_audio is not None else None
    return BenchmarkRow(
        candidate=candidate.key,
        passage=passage.id,
        mode=result.mode,
        wav=str(result.wav),
        duration_s=round(duration_s, 3),
        wall_s=round(wall_s, 3),
        process_s=round(process_s, 3),
        rtf=round(wall_s / duration_s, 4) if duration_s else 0.0,
        peak_vram_mib=monitor.peak_mib,
        sample_rate=sample_rate,
        wer=score.wer,
        substitutions=score.substitutions,
        omissions=score.omissions,
        insertions=score.insertions,
        numbers_ok=score.numbers_ok,
        acronyms_ok=score.acronyms_ok,
        transcript_ratio=score.transcript_ratio,
        long_form_completed=score.long_form_completed,
        speaker_similarity=round(cosine, 4) if cosine is not None else None,
        speaker_similarity_skipped=None if ref_audio is not None else NO_REFERENCE,
        transcript=" ".join(words),
    )


# --- report ---


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "NO"
    return str(value)


def report_markdown(report: BenchmarkReport) -> str:
    reference = report.ref_audio or f"none (speaker similarity skipped: {NO_REFERENCE})"
    lines = [
        "# TTS benchmark",
        "",
        f"- created: {report.created_at} on {report.host}",
        f"- gpu: {report.gpu}",
        f"- other tenants on the card: {', '.join(report.other_tenants) or 'none'}",
        f"- asr: {report.asr}; seed {report.seed}",
        f"- creator reference: {reference}",
        "",
        "## Rows",
        "",
        "| candidate | passage | mode | wer | subs | omit | ins | numbers | acronyms | duration_s "
        "| wall_s | rtf | peak_vram_mib | sr | long_form | speaker_sim |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.rows:
        lines.append(
            f"| {r.candidate} | {r.passage} | {r.mode} | {r.wer:.3f} | {r.substitutions} "
            f"| {r.omissions} | {r.insertions} | {_cell(r.numbers_ok)} | {_cell(r.acronyms_ok)} "
            f"| {r.duration_s:.1f} | {r.wall_s:.1f} | {r.rtf:.3f} | {_cell(r.peak_vram_mib)} "
            f"| {r.sample_rate} | {_cell(r.long_form_completed)} | {_cell(r.speaker_similarity)} |"
        )
    if report.skipped:
        lines += ["", "## Skipped", ""]
        lines += [f"- {s.candidate} / {s.passage}: {s.reason}" for s in report.skipped]
    lines += ["", "## Excluded by licence (never synthesized)", ""]
    lines += [f"- {e.key} ({e.label}): {e.license} — {e.reason}" for e in report.exclusions]
    return "\n".join(lines) + "\n"


def write_report(report: BenchmarkReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "report.json", out_dir / "report.md"
    json_path.write_text(report.to_json())
    md_path.write_text(report_markdown(report))
    return json_path, md_path


# --- CLI ---


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="TTS benchmark behind the licence gate")
    ap.add_argument("--out", type=Path, default=REPO / "output" / "explainer" / "tts_bench")
    ap.add_argument("--ref-audio", type=Path, default=None, help="creator reference wav")
    ap.add_argument("--ref-text", default=None, help="transcript of --ref-audio (Qwen3 Base)")
    ap.add_argument("--candidates", default="", help="comma-separated keys; default: selectable")
    ap.add_argument("--passages", default="", help="comma-separated passage ids; default: all")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--wait-timeout-s", type=float, default=VRAM_WAIT_TIMEOUT_S)
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    keys = [k for k in args.candidates.split(",") if k]
    unknown = [k for k in keys if k not in CANDIDATE_BY_KEY]
    if unknown:
        sys.stderr.write(f"unknown candidates: {', '.join(unknown)}\n")
        return 2
    chosen = (
        [CANDIDATE_BY_KEY[k] for k in keys] if keys else [c for c in CANDIDATES if selectable(c)]
    )
    try:
        for candidate in chosen:
            _refuse(candidate)
    except LicenseGateError as exc:
        sys.stderr.write(f"{exc}\n")
        return 3
    passages = load_passages()
    if args.passages:
        wanted = set(args.passages.split(","))
        passages = [p for p in passages if p.id in wanted]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out / stamp
    try:
        report = run_benchmark(
            chosen,
            passages,
            out_dir,
            ref_audio=args.ref_audio,
            ref_text=args.ref_text,
            seed=args.seed,
            wait_timeout_s=args.wait_timeout_s,
            log=lambda msg: sys.stderr.write(f"{msg}\n"),
            checkpoint=lambda partial: write_report(partial, out_dir),
        )
    except (BenchmarkError, LicenseGateError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    json_path, md_path = write_report(report, out_dir)
    sys.stdout.write(report_markdown(report))
    sys.stdout.write(f"wrote {json_path} and {md_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
