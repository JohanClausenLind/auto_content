"""The TTS benchmark offline: the licence gate, transcript scoring, the report; no GPU, no model."""

from __future__ import annotations

import json
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from content_factory.explainer import tts_bench
from content_factory.explainer.tts_bench import (
    CANDIDATES,
    CLONE_NEEDS_REFERENCE,
    NO_REFERENCE,
    BenchmarkError,
    Candidate,
    LicenseGateError,
    Passage,
    SynthRequest,
    SynthResult,
    VramMonitor,
    load_passages,
    normalize_words,
    report_markdown,
    run_benchmark,
    score_transcript,
    selectable,
    wait_for_gpu,
    word_edit_counts,
    write_report,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "explainer"
EXCLUDED = [c for c in CANDIDATES if not selectable(c)]
SELECTABLE = [c for c in CANDIDATES if selectable(c)]


class SpyRunner:
    def __init__(self) -> None:
        self.calls: list[SynthRequest] = []

    def __call__(self, req: SynthRequest) -> SynthResult:
        self.calls.append(req)
        _silence(req.out, seconds=1.0)
        return SynthResult(req.out, 1.0, 24000, 0.5, "fake")


def _silence(path: Path, *, seconds: float, rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def _fake_candidate(key: str, runner: SpyRunner, **overrides: object) -> Candidate:
    base = Candidate(key, key, "MIT", True, "fake", runner)
    return replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """No nvidia-smi, no faster-whisper, no executor: everything the run touches is injected."""
    monkeypatch.setattr(tts_bench, "gpu_name", lambda: "fake gpu")
    monkeypatch.setattr(tts_bench, "gpu_tenants", lambda: ["1972, other-workspace, 1522"])
    return {
        "asr": lambda _wav: ["spoken", "words"],
        "vram_used_mib": lambda: 4000,
        "vram_free_mib": lambda: 20000,
        "similarity": lambda _ref, _wav: 0.9,
    }


# --- the gate ---


def test_selectable_is_exactly_the_commercial_output_flag() -> None:
    assert {c.key for c in SELECTABLE} == {
        "qwen3-tts-base",
        "qwen3-tts-customvoice",
        "qwen3-tts-voicedesign",
        "chatterbox-turbo",
    }
    assert {c.key for c in EXCLUDED} == {"voxtral-tts", "breeze-tts2", "fish-s2-pro"}
    assert all(c.runner is None for c in EXCLUDED)


@pytest.mark.parametrize("excluded", EXCLUDED, ids=lambda c: c.key)
def test_gate_refuses_an_excluded_candidate_even_with_a_runner_attached(
    excluded: Candidate, tmp_path: Path, offline: dict[str, object]
) -> None:
    spy = SpyRunner()
    armed = replace(excluded, runner=spy)
    with pytest.raises(LicenseGateError) as info:
        run_benchmark([armed], load_passages(), tmp_path, **offline)  # type: ignore[arg-type]
    assert excluded.license in str(info.value)
    assert excluded.key in str(info.value)
    assert spy.calls == []
    assert not list(tmp_path.iterdir())


def test_gate_fires_before_any_selectable_candidate_in_the_same_run_synthesizes(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    good, bad = SpyRunner(), SpyRunner()
    candidates = [_fake_candidate("ok", good), replace(EXCLUDED[0], runner=bad)]
    with pytest.raises(LicenseGateError):
        run_benchmark(candidates, load_passages()[:1], tmp_path, **offline)  # type: ignore[arg-type]
    assert good.calls == [] and bad.calls == []


def test_cli_refuses_an_explicitly_named_excluded_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = tts_bench.main(["--out", str(tmp_path), "--candidates", "breeze-tts2"])
    assert code == 3
    err = capsys.readouterr().err
    assert "BreezeBlue Research and Non-Commercial License" in err
    assert not list(tmp_path.iterdir())


# --- normalisation and scoring ---


def test_percent_forms_and_digit_groups_normalise_alike() -> None:
    assert normalize_words("46.3 percent") == normalize_words("46.3%") == ["46.3", "percent"]
    assert normalize_words("1,250 samples") == normalize_words("1250 samples.")
    assert normalize_words("Forty six point three percent") == ["46.3", "percent"]
    assert normalize_words("In nineteen sixty seven,") == normalize_words("in 1967")
    assert normalize_words("twenty twenty four") == ["2024"]
    assert normalize_words("two thousand twenty five") == ["2025"]
    assert normalize_words("one thousand two hundred and fifty") == ["1250"]
    assert normalize_words("zero point five milliseconds") == ["0.5", "milliseconds"]
    assert normalize_words("between seventy and ninety") == ["between", "70", "and", "90"]
    assert normalize_words("The point is") == ["the", "point", "is"]
    # faster-whisper's word list, verbatim from a run: leading punctuation on the next token
    assert normalize_words("46 .3 % of 1 ,250 samples exceeded 0 .5") == [
        "46.3",
        "percent",
        "of",
        "1250",
        "samples",
        "exceeded",
        "0.5",
    ]
    assert normalize_words("It still takes 60. 80 ms") == ["it", "still", "takes", "60", "80", "ms"]
    assert normalize_words("the cog has twenty, one turn") == [
        "the",
        "cog",
        "has",
        "20",
        "1",
        "turn",
    ]
    assert normalize_words("has 20, 1 turn") == normalize_words("has twenty, one turn")
    assert normalize_words("speed-up") == ["speed", "up"]


def test_spelled_acronyms_and_case_normalise_alike() -> None:
    assert normalize_words("The G P U and the CPU") == ["the", "gpu", "and", "the", "cpu"]
    assert normalize_words("by Q 3 2025") == normalize_words("by Q3 2025") == ["by", "q3", "2025"]
    assert normalize_words("PCIe 4.0 lanes") == ["pcie", "4.0", "lanes"]
    assert normalize_words("returns JSON") == normalize_words("returns Jason")
    assert normalize_words("a processor") == ["a", "processor"]


def test_edit_counts_separate_substitutions_omissions_and_insertions() -> None:
    ref = "your program takes 100 milliseconds".split()
    assert word_edit_counts(ref, ref) == (0, 0, 0)
    assert word_edit_counts(ref, "your program takes 100".split()) == (0, 1, 0)
    assert word_edit_counts(ref, "your program took 100 milliseconds".split()) == (1, 0, 0)
    assert word_edit_counts(ref, "your own program takes 100 milliseconds".split()) == (0, 0, 1)


def test_omissions_and_numbers_on_a_hand_written_transcript() -> None:
    passage = next(p for p in load_passages() if p.id == "numbers")
    exact = score_transcript(passage, passage.text.split())
    assert exact.wer == 0 and exact.omissions == 0 and exact.numbers_ok is True
    dropped = "In 2024, 46.3% of 1,250 samples exceeded; by Q3 2025 that fell to twelve percent."
    score = score_transcript(passage, dropped.split())
    assert score.omissions == 2
    assert score.substitutions == 0 and score.insertions == 0
    assert score.numbers_ok is False
    assert score.wer == pytest.approx(2 / 18, abs=1e-4)
    assert score.acronyms_ok is None and score.long_form_completed is None


def test_acronyms_check_needs_every_acronym_in_the_transcript() -> None:
    passage = next(p for p in load_passages() if p.id == "acronyms")
    heard = "The G P U, CPU and NVMe SSD share PCIe 4.0 lanes; the API returns Jason over HTTPS."
    assert score_transcript(passage, heard.split()).acronyms_ok is True
    garbled = heard.replace("HTTPS", "H T T P")
    assert score_transcript(passage, garbled.split()).acronyms_ok is False


def test_long_form_completed_is_transcript_length_within_ten_percent() -> None:
    passage = next(p for p in load_passages() if p.long_form)
    words = passage.text.split()
    assert score_transcript(passage, words).long_form_completed is True
    assert score_transcript(passage, words[: int(len(words) * 0.85)]).long_form_completed is False
    assert score_transcript(passage, words[: int(len(words) * 0.95)]).long_form_completed is True


def test_passages_fixture_holds_the_five_kinds_and_the_amdahl_script() -> None:
    passages = load_passages()
    assert [p.id for p in passages] == ["amdahl", "numbers", "acronyms", "emphasis", "long_form"]
    script = json.loads((FIXTURES / "amdahl" / "script.json").read_text())
    assert passages[0].text == " ".join(s["spoken_text"] for s in script["segments"])
    assert sum(1 for p in passages if p.long_form) == 1
    assert 550 <= len(passages[4].text.split()) <= 700
    stressed = [w for w in passages[3].text.split() if w.strip(".,;").isupper()]
    assert len(stressed) == 2


# --- gpu tenancy ---


def test_vram_monitor_reports_the_peak_over_the_baseline() -> None:
    readings = iter([4000, 9000, 12000, 11000])
    monitor = VramMonitor(lambda: next(readings, 11000), interval_s=0.01)
    with monitor:
        while len(monitor.samples) < 3:
            pass
    assert monitor.baseline_mib == 4000
    assert monitor.peak_mib == 8000


def test_wait_for_gpu_blocks_until_the_target_is_free_and_gives_up_on_time() -> None:
    frees = iter([5000, 9000, 19000])
    slept: list[float] = []
    assert wait_for_gpu(lambda: next(frees), sleep=slept.append, clock=lambda: 0.0) == 19000
    assert len(slept) == 2
    ticks = iter([0.0, 0.0, 1000.0])
    with pytest.raises(BenchmarkError, match="MiB of VRAM free"):
        wait_for_gpu(lambda: 5000, sleep=lambda _s: None, clock=lambda: next(ticks), timeout_s=10)
    assert wait_for_gpu(lambda: None, sleep=lambda _s: None) is None


# --- the report ---


def test_report_has_one_row_per_pair_and_the_exclusions_section(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    a, b = SpyRunner(), SpyRunner()
    candidates = [_fake_candidate("fake-a", a), _fake_candidate("fake-b", b)]
    passages = load_passages()
    report = run_benchmark(candidates, passages, tmp_path, **offline)  # type: ignore[arg-type]
    assert len(report.rows) == 2 * 5
    assert {(r.candidate, r.passage) for r in report.rows} == {
        (c.key, p.id) for c in candidates for p in passages
    }
    assert len(a.calls) == len(b.calls) == 5
    assert all(r.speaker_similarity is None for r in report.rows)
    assert all(r.speaker_similarity_skipped == NO_REFERENCE for r in report.rows)
    assert all(r.duration_s == 1.0 and r.sample_rate == 24000 for r in report.rows)
    assert all(r.wall_s == 0.5 and r.rtf == 0.5 and r.peak_vram_mib == 0 for r in report.rows)
    md = report_markdown(report)
    table_rows = [line for line in md.splitlines() if line.startswith("| fake-")]
    assert len(table_rows) == 10
    assert "## Excluded by licence (never synthesized)" in md
    for excluded in EXCLUDED:
        assert f"- {excluded.key} ({excluded.label}): {excluded.license}" in md
    assert NO_REFERENCE in md
    json_path, md_path = write_report(report, tmp_path)
    data = json.loads(json_path.read_text())
    assert len(data["rows"]) == 10 and len(data["exclusions"]) == 3
    assert md_path.read_text() == md


def test_cloning_candidate_is_skipped_with_a_reason_when_no_reference_is_given(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    spy = SpyRunner()
    clone = _fake_candidate("clone", spy, needs_reference=True)
    report = run_benchmark([clone], load_passages()[:2], tmp_path, **offline)  # type: ignore[arg-type]
    assert report.rows == [] and spy.calls == []
    assert [(s.candidate, s.passage, s.reason) for s in report.skipped] == [
        ("clone", "*", CLONE_NEEDS_REFERENCE)
    ]
    assert f"- clone / *: {CLONE_NEEDS_REFERENCE}" in report_markdown(report)


def test_reference_recording_turns_on_cloning_and_speaker_similarity(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    ref = tmp_path / "creator.wav"
    _silence(ref, seconds=6.0)
    spy = SpyRunner()
    clone = _fake_candidate("clone", spy, needs_reference=True)
    passage = Passage("p", "script", "one two three")
    report = run_benchmark(
        [clone],
        [passage],
        tmp_path / "out",
        ref_audio=ref,
        ref_text="hello",
        **offline,  # type: ignore[arg-type]
    )
    assert spy.calls[0].ref_audio == ref and spy.calls[0].ref_text == "hello"
    assert report.rows[0].speaker_similarity == 0.9
    assert report.rows[0].speaker_similarity_skipped is None
    assert report.ref_audio == str(ref)


class FlakyRunner(SpyRunner):
    """Fails with the given messages first, then behaves like SpyRunner."""

    def __init__(self, *failures: str) -> None:
        super().__init__()
        self.failures = list(failures)

    def __call__(self, req: SynthRequest) -> SynthResult:
        if self.failures:
            self.calls.append(req)
            raise BenchmarkError(self.failures.pop(0))
        return super().__call__(req)


def test_cuda_oom_from_another_tenant_waits_for_the_card_and_retries_once(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    runner = FlakyRunner("qwen3tts failed: torch.OutOfMemoryError: CUDA out of memory.")
    waits: list[int] = []
    offline["vram_free_mib"] = lambda: waits.append(20000) or 20000
    report = run_benchmark(
        [_fake_candidate("flaky", runner)],
        load_passages()[:1],
        tmp_path,
        **offline,  # type: ignore[arg-type]
    )
    assert len(runner.calls) == 2 and len(report.rows) == 1 and report.skipped == []
    assert len(waits) == 2


def test_a_row_that_keeps_failing_is_recorded_and_the_run_continues(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    runner = FlakyRunner(
        "chatterbox failed: CUDA out of memory", "chatterbox failed: CUDA out of memory"
    )
    passages = load_passages()[:3]
    report = run_benchmark(
        [_fake_candidate("flaky", runner)],
        passages,
        tmp_path,
        **offline,  # type: ignore[arg-type]
    )
    assert [r.passage for r in report.rows] == ["numbers", "acronyms"]
    assert [(s.candidate, s.passage) for s in report.skipped] == [("flaky", "amdahl")]
    assert "out of memory" in report.skipped[0].reason
    other = FlakyRunner("chatterbox failed: boom")
    report = run_benchmark(
        [_fake_candidate("other", other)],
        passages[:1],
        tmp_path,
        **offline,  # type: ignore[arg-type]
    )
    assert report.rows == [] and len(other.calls) == 1 and report.skipped[0].reason.endswith("boom")


def test_checkpoint_receives_the_report_after_every_row(
    tmp_path: Path, offline: dict[str, object]
) -> None:
    seen: list[int] = []
    run_benchmark(
        [_fake_candidate("a", SpyRunner())],
        load_passages()[:3],
        tmp_path,
        checkpoint=lambda report: seen.append(len(report.rows)),
        **offline,  # type: ignore[arg-type]
    )
    assert seen == [1, 2, 3]
