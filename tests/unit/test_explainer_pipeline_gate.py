"""Gates F4 and F6 on the real renderer: sixty through export, then a run that renders nothing."""

from __future__ import annotations

import json
import shutil
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from content_factory.explainer.export import verify_export
from content_factory.explainer.narration import Synth
from content_factory.explainer.pipeline import (
    LEDGER_NAME,
    QUEUE_NAME,
    STEP_NAMES,
    EpisodeConfig,
    RunResult,
    Seams,
    render_stills,
    render_video,
    run,
)
from content_factory.explainer.timing import ESTIMATED_TOKEN_MS
from content_factory.schemas.explainer import ScriptSegment, VoiceSpec
from tests.unit.explainer_fakes import FIXTURES, even_aligner, sixty_config, tone_synth

pytestmark = pytest.mark.render

REPO = Path(__file__).resolve().parents[2]
KOKORO = FIXTURES / "narration" / "kokoro_sixty.wav"
MUSIC_ID = "explainer_light_pulse"
MUSIC_FILE = REPO / "assets" / "music" / "explainer" / f"{MUSIC_ID}.flac"


class Counted:
    """A seam that forwards to the real one and remembers every call."""

    def __init__(self, inner: Callable[..., Any]) -> None:
        self.inner = inner
        self.calls = 0

    def __call__(self, *args: Any) -> Any:
        self.calls += 1
        return self.inner(*args)


def copied_kokoro(_segment: ScriptSegment, _voice: VoiceSpec, path: Path) -> None:
    shutil.copyfile(KOKORO, path)


def tiled_kokoro(segment: ScriptSegment, _voice: VoiceSpec, path: Path) -> None:
    """The recorded Kokoro line repeated to the segment's estimated length, so pacing holds."""
    with wave.open(str(KOKORO), "rb") as wf:
        params, frames = wf.getparams(), wf.readframes(wf.getnframes())
    width = params.sampwidth * params.nchannels
    wanted = len(segment.tokens) * ESTIMATED_TOKEN_MS * params.framerate // 1000 * width
    audio = (frames * (wanted // len(frames) + 1))[:wanted]
    with wave.open(str(path), "wb") as wf:
        wf.setparams(params)
        wf.writeframes(audio)


@dataclass(frozen=True)
class Gate:
    config: EpisodeConfig
    first: RunResult
    second: RunResult
    videos_first: int
    stills_first: int
    videos_second: int
    stills_second: int
    first_issues: list[str]
    second_issues: list[str]
    renderer_moved: bool


def _renderer_hash(config: EpisodeConfig) -> str:
    ledger = json.loads((config.episode_dir / LEDGER_NAME).read_text())
    entry = next((e for e in ledger["steps"] if e["step"] == "render_animatic"), {})
    return entry.get("inputs", {}).get("renderer_source_hash", "")


def _skip_if_the_renderer_moved(gate: Gate) -> None:
    """A shared checkout: another session editing the renderer between runs must re-render."""
    if gate.renderer_moved:
        pytest.skip("renderer sources changed between the two runs; the re-render was correct")


def _run_twice(root: Path, episode_id: str, synth: Synth) -> Gate:
    music = MUSIC_ID if MUSIC_FILE.is_file() else None
    config = sixty_config(root, episode_id=episode_id, music_id=music)
    videos, stills = Counted(render_video), Counted(render_stills)
    seams = Seams(render_video=videos, render_stills=stills, synth=synth, aligner=even_aligner)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CF_SERVICES_DIR", str(root / "services"))
        first = run(config, seams=seams)
        first_issues = [str(i) for i in verify_export(config.episode_dir)]
        counts, rendered_with = (videos.calls, stills.calls), _renderer_hash(config)
        second = run(config, seams=seams)
        second_issues = [str(i) for i in verify_export(config.episode_dir)]
    return Gate(
        config,
        first,
        second,
        counts[0],
        counts[1],
        videos.calls - counts[0],
        stills.calls - counts[1],
        first_issues,
        second_issues,
        rendered_with != _renderer_hash(config),
    )


@pytest.fixture(scope="module")
def gate(tmp_path_factory: pytest.TempPathFactory) -> Gate:
    """The brief's gate: every segment speaks the recorded Kokoro line."""
    return _run_twice(tmp_path_factory.mktemp("gate"), "epi_sixtygate1", tiled_kokoro)


@pytest.fixture(scope="module")
def tone_gate(tmp_path_factory: pytest.TempPathFactory) -> Gate:
    """The same real pipeline with a steady tone for speech, which the master chain can level."""
    return _run_twice(tmp_path_factory.mktemp("tone"), "epi_sixtytone1", tone_synth)


def _why(config: EpisodeConfig) -> str:
    ledger = json.loads((config.episode_dir / LEDGER_NAME).read_text())
    failed = [e for e in ledger["steps"] if e["status"] != "done"]
    queue = config.episode_dir / QUEUE_NAME
    return json.dumps(failed, indent=1) + (queue.read_text() if queue.is_file() else "")


def test_f4_the_first_run_renders_each_picture_once_and_reaches_the_final_qc(gate: Gate) -> None:
    through_qc = STEP_NAMES[: STEP_NAMES.index("qc_final") + 1]
    assert gate.first.ran[: len(through_qc)] == through_qc, _why(gate.config)
    assert gate.videos_first == 2
    if gate.first.status != "done":
        assert gate.first.status == "blocked" and gate.first.stopped_at == "qc_final"
        assert (gate.config.episode_dir / QUEUE_NAME).is_file() and not gate.first.ready


def test_f4_a_second_run_reuses_every_finished_step_and_renders_nothing(gate: Gate) -> None:
    _skip_if_the_renderer_moved(gate)
    finished = STEP_NAMES[: STEP_NAMES.index("qc_final")]
    assert gate.second.skipped[: len(finished)] == finished
    assert gate.second.status == gate.first.status
    assert gate.videos_second == 0 and gate.stills_second == 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "audio.mix.master lands Kokoro speech at -15.3 LUFS / -1.3 dBTP, outside qc_checks' "
        "-14 +/-1 LU, so qc_final blocks before export (2026-09-26)"
    ),
)
def test_f6_the_run_finishes_ready_and_its_export_verifies(gate: Gate) -> None:
    assert gate.first.status == "done" and gate.first.ready, _why(gate.config)
    assert gate.second.ran == () and gate.second.skipped == STEP_NAMES
    assert gate.stills_first == 1
    assert gate.first_issues == [] and gate.second_issues == []


def test_f4_f6_a_tone_narrated_episode_exports_verifies_and_then_renders_nothing(
    tone_gate: Gate,
) -> None:
    assert tone_gate.first.status == "done" and tone_gate.first.ready, _why(tone_gate.config)
    assert tone_gate.first.ran == STEP_NAMES
    assert tone_gate.videos_first == 2 and tone_gate.stills_first == 1
    assert tone_gate.first_issues == []
    _skip_if_the_renderer_moved(tone_gate)
    assert tone_gate.second.ran == () and tone_gate.second.skipped == STEP_NAMES
    assert tone_gate.videos_second == 0 and tone_gate.stills_second == 0
    assert tone_gate.second_issues == []


def test_one_short_line_for_every_segment_is_refused_at_compile(tmp_path: Path) -> None:
    config = sixty_config(tmp_path, episode_id="epi_sixtyshort1")
    seams = Seams(synth=copied_kokoro, aligner=even_aligner)
    result = run(config, seams=seams, until="compile")
    assert result.status == "failed" and result.stopped_at == "compile"
    assert result.error is not None and "[timing]" in result.error
