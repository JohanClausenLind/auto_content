"""Giving the card to the flashcards agent, and getting the render back.

The properties that matter are the ones an operator would be angry about if they broke: a session
that needs no room stops nothing, a parked run comes back as the film it was rather than as its
workflow's defaults, a run started mid-session cannot steal the VRAM back, and a crashed tenant
cannot park a render for ever.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from content_factory.runners import registry
from content_factory.services import gpu_priority


@pytest.fixture(autouse=True)
def _isolated_services_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path / "services"))
    monkeypatch.setattr(registry, "PID_ALIVE", lambda _pid: True)


def _register(workflow: str, *, step: str, argv: list[str], pid: int = 4242) -> Path:
    path = registry.runs_dir() / f"{registry.run_key(workflow, pid)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_key": registry.run_key(workflow, pid),
                "pid": pid,
                "workflow": workflow,
                "project_dir": "/tmp/prj",
                "started_at": time.time(),
                "step": step,
                "argv": argv,
            }
        )
    )
    return path


def test_enough_free_stops_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common case: a session opens, the card is idle, no film is disturbed."""
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 20.9)
    run = _register("hybrid-video", step="generate_anchor", argv=["content-factory", "run-local"])

    outcome = gpu_priority.yield_gpu(need_gib=17.5, reason="flashcards", stop_services=False)

    assert outcome.ok
    assert outcome.parked == []
    assert "nothing stopped" in outcome.note
    assert "stop_requested" not in json.loads(run.read_text())


def test_no_nvidia_smi_is_not_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: None)
    outcome = gpu_priority.yield_gpu(need_gib=17.5, stop_services=False)
    assert outcome.ok
    assert "nothing to arbitrate" in outcome.note


def test_a_held_card_parks_the_run_at_a_stage_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Krea2 is resident, flashcards wants 17.5 GiB: the run is asked to stop, not killed."""
    free = iter([3.0, 20.0])  # before the stop, and after the run let go
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: next(free))
    run = _register(
        "hybrid-video",
        step="generate_keyframes",
        argv=["content-factory", "run-local", "hybrid-video", "--style", "watercolour"],
    )

    def _run_exits(_seconds: float) -> None:
        run.unlink()  # the runner saw stop_requested at the next boundary and released

    outcome = gpu_priority.yield_gpu(
        need_gib=17.5, reason="flashcards", stop_services=False, sleep=_run_exits
    )

    assert outcome.ok
    assert [p.step for p in outcome.parked] == ["generate_keyframes"]
    assert "parked 1 run(s) at a stage boundary" in outcome.note
    claim = gpu_priority.read_claim()
    assert claim is not None
    assert claim.reason == "flashcards"
    assert claim.parked[0].workflow == "hybrid-video"


def test_a_stage_that_will_not_yield_is_signalled_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video stage can hold the card for ten minutes. Priority means not waiting for it."""
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 2.0)
    _register("hybrid-video", step="generate_video", argv=["content-factory", "run-local"])
    forced: list[dict] = []
    monkeypatch.setattr(
        "content_factory.services.stop.stop",
        lambda **kw: forced.append(kw) or [],
    )
    clock = iter([0.0, 0.0, 999.0])

    outcome = gpu_priority.yield_gpu(
        need_gib=17.5,
        deadline_s=90.0,
        stop_services=False,
        now=lambda: next(clock),
        sleep=lambda _s: None,
    )

    assert forced and forced[0]["targets"] == ["runs"]
    assert forced[0]["graceful"] is False
    assert "by signal" in outcome.note
    assert not outcome.ok  # 2.0 GiB free: honest about having failed to make room


def test_resume_replays_the_film_that_was_running_not_the_defaults() -> None:
    parked = gpu_priority.ParkedRun(
        workflow="hybrid-video",
        project_dir="/tmp/prj",
        step="generate_keyframes",
        argv=[
            "content-factory",
            "run-local",
            "hybrid-video",
            "--style",
            "watercolour",
            "--story",
            "fixtures/story/wind_2024.json",
            "--from",
            "plan_story",
        ],
        run_key="hybrid-video-4242",
        parked_at=0.0,
    )

    command = parked.resume_command()

    assert "--style" in command and "watercolour" in command
    assert "--story" in command
    assert command[-2:] == ["--from", "generate_keyframes"]
    assert command.count("--from") == 1  # the original resume point was replaced, not appended
    assert "plan_story" not in command
    assert "--project-dir" in command


def test_resume_starts_what_was_parked_and_drops_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 2.0)
    run = _register("hybrid-video", step="compose_video", argv=["content-factory", "run-local"])
    gpu_priority.yield_gpu(
        need_gib=17.5, stop_services=False, sleep=lambda _s: run.unlink(), deadline_s=5.0
    )
    started: list[list[str]] = []
    monkeypatch.setattr(
        gpu_priority, "SPAWN", lambda cmd, _cwd, _log: started.append(list(cmd)) or 1
    )

    outcome = gpu_priority.resume_gpu()

    assert len(started) == 1
    assert started[0][-2:] == ["--from", "compose_video"]
    assert outcome.note == "resumed 1 run(s)"
    assert gpu_priority.read_claim() is None


def test_resume_is_safe_to_call_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the session hook and a timer may call it; the second must be a no-op,
    not a second render."""
    monkeypatch.setattr(gpu_priority, "SPAWN", lambda *_a: 1)
    assert gpu_priority.resume_gpu().note == "no claim; nothing to resume"

    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 20.0)
    gpu_priority.yield_gpu(need_gib=1.0, stop_services=False)  # a claim that parked nothing
    assert "no run had been parked" in gpu_priority.resume_gpu().note
    assert gpu_priority.resume_gpu().note == "no claim; nothing to resume"


def test_print_only_keeps_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 2.0)
    run = _register("hybrid-video", step="generate_anchor", argv=["content-factory", "run-local"])
    gpu_priority.yield_gpu(need_gib=17.5, stop_services=False, sleep=lambda _s: run.unlink())

    outcome = gpu_priority.resume_gpu(print_only=True)

    assert outcome.commands and outcome.resumed
    assert gpu_priority.read_claim() is not None  # nothing was started, so nothing was released


def test_a_cron_safety_net_cannot_interrupt_a_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timer exists for the tenant that crashed, not the one still studying."""
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 2.0)
    run = _register("hybrid-video", step="generate_anchor", argv=["content-factory", "run-local"])
    gpu_priority.yield_gpu(need_gib=17.5, stop_services=False, sleep=lambda _s: run.unlink())
    started: list[list[str]] = []
    monkeypatch.setattr(
        gpu_priority, "SPAWN", lambda cmd, _cwd, _log: started.append(list(cmd)) or 1
    )

    fresh = gpu_priority.resume_gpu(stale_after_s=3600.0)
    assert started == []
    assert "younger than" in fresh.note
    assert gpu_priority.read_claim() is not None

    stale = gpu_priority.resume_gpu(stale_after_s=3600.0, now=lambda: time.time() + 3601)
    assert len(started) == 1
    assert stale.note == "resumed 1 run(s)"


def test_a_crashed_tenant_cannot_park_a_render_for_ever(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 20.0)
    gpu_priority.yield_gpu(need_gib=1.0, reason="flashcards", stop_services=False)

    assert gpu_priority.blocking_claim(max_hold_s=7200.0, now=time.time) is not None
    assert gpu_priority.blocking_claim(max_hold_s=7200.0, now=lambda: time.time() + 7201) is None


def test_a_run_refuses_to_start_while_the_card_is_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Priority that only works one way is not priority."""
    from content_factory.runners.local import LocalRunError, _refuse_while_gpu_claimed

    monkeypatch.delenv("CF_IGNORE_GPU_CLAIM", raising=False)
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 20.0)
    gpu_priority.yield_gpu(need_gib=1.0, reason="flashcards", stop_services=False)

    with pytest.raises(LocalRunError, match="claimed by 'flashcards'"):
        _refuse_while_gpu_claimed()

    monkeypatch.setenv("CF_IGNORE_GPU_CLAIM", "1")
    _refuse_while_gpu_claimed()  # the deliberate override


def test_the_model_servers_are_unloaded_because_that_is_where_the_gigabytes_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping the run does not unload HiDream or ComfyUI; those hold the VRAM."""
    monkeypatch.setattr(gpu_priority, "FREE_VRAM_GIB", lambda: 20.0)
    freed: list[bool] = []
    monkeypatch.setattr(
        "content_factory.services.local.free_the_gpu", lambda: freed.append(True) or {}
    )

    gpu_priority.yield_gpu(need_gib=99.0, stop_services=True, sleep=lambda _s: None, deadline_s=0.0)

    assert freed == [True]
