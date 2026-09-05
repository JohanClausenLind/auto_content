"""One button stops everything: the run, the processes, the GPU, compose — and nothing else.

The machine is described rather than had: process tables are fixtures, signals are recorded, and
the only real processes involved are the test's own. What matters is what a stop *decides* —
whose pid it signals, what it leaves alone, and what a stopped run leaves behind on disk.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from content_factory.runners import registry
from content_factory.runners.local import make_context, run_plan
from content_factory.runners.registry import RunStopped
from content_factory.schemas.dag import Stage
from content_factory.services import stop as stop_svc
from content_factory.services.stop import ProcInfo, Stopper, classify

REPO = Path(__file__).resolve().parents[2]


def proc(pid: int, ppid: int, *args: str, cwd: str | None = str(REPO)) -> ProcInfo:
    return ProcInfo(pid=pid, ppid=ppid, args=tuple(args), cwd=cwd)


class FakeMachine:
    """A process table, the signals sent to it, and who died when."""

    def __init__(self, procs: list[ProcInfo]) -> None:
        self.procs = procs
        self.sent: list[tuple[int, int]] = []
        self.dead: set[int] = set()
        self.stubborn: set[int] = set()  # pids that ignore SIGTERM
        self.clock = 0.0

    def processes(self) -> list[ProcInfo]:
        return [p for p in self.procs if p.pid not in self.dead]

    def kill(self, pid: int, sig: int) -> None:
        self.sent.append((pid, sig))
        if sig == signal.SIGTERM and pid not in self.stubborn:
            self.dead.add(pid)
        if sig == signal.SIGKILL:
            self.dead.add(pid)

    def alive(self, pid: int) -> bool:
        return pid not in self.dead

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def monotonic(self) -> float:
        return self.clock

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stop_svc, "PROCESSES", self.processes)
        monkeypatch.setattr(stop_svc, "SIGNAL", self.kill)
        monkeypatch.setattr(registry, "PID_ALIVE", self.alive)


# -- what counts as ours ---------------------------------------------------------------------
def test_only_this_checkouts_own_commands_are_ever_touched() -> None:
    """The narrow rule is the safety rule: this host runs other projects and other checkouts,
    and an operator's grep for a command is not that command."""
    assert classify(proc(2, 1, "uv", "run", "content-factory", "worker")) == "worker"
    assert classify(proc(3, 2, f"{REPO}/.venv/bin/content-factory", "serve", "--reload")) == "api"
    assert classify(proc(4, 1, "node", f"{REPO}/node_modules/.bin/vite")) == "web"
    assert classify(proc(5, 1, "uv", "run", "content-factory", "make", "picture-story")) == "run"
    assert classify(proc(6, 1, "uv", "run", "content-factory", "mcp")) == "mcp"

    # Another project's server, in another directory, that happens to be a python uvicorn.
    assert classify(proc(7, 1, "/other/.venv/bin/uvicorn", "app:app", cwd="/other")) is None
    # An operator grepping for the words. Same words, no argv position: not a run.
    assert classify(proc(8, 1, "grep", "-rn", "content-factory make", "python/")) is None
    # Ours by name but not a background program: `stop` and `doctor` are how you ask about them.
    assert classify(proc(9, 1, "uv", "run", "content-factory", "doctor")) is None
    assert classify(proc(10, 1, "uv", "run", "content-factory", "stop")) is None
    # A worker started the way the integration tests start one, by module path.
    module = "from content_factory.workflows.worker import main; main('control')"
    assert classify(proc(11, 1, f"{REPO}/.venv/bin/python3", "-c", module, cwd="/tmp")) == "worker"
    # The API started through its documented uvicorn entry point rather than the CLI.
    assert classify(proc(12, 1, "uvicorn", "apps.api.main:app", "--port", "8000")) == "api"
    # And the worker through its own: `uv run python apps/workers/main.py --queue control`.
    assert classify(proc(13, 1, "python", "apps/workers/main.py", "--queue", "control")) == "worker"


def test_a_launcher_and_the_process_it_launched_are_one_thing(monkeypatch) -> None:
    machine = FakeMachine(
        [
            proc(100, 1, "uv", "run", "content-factory", "worker"),
            proc(101, 100, f"{REPO}/.venv/bin/content-factory", "worker"),
            proc(102, 1, "uv", "run", "content-factory", "serve"),
        ]
    )
    machine.install(monkeypatch)
    stopper = Stopper(sleep=machine.sleep, monotonic=machine.monotonic)
    stopper.stop_apps()
    signalled = [pid for pid, sig in machine.sent if sig == signal.SIGTERM]
    assert signalled == [100, 101, 102]  # the worker's child through its parent's tree, once
    assert [o.subject for o in stopper.outcomes] == [
        "Temporal worker (pid 100)",
        "API server (pid 102)",
    ]


def test_the_mcp_server_is_reported_and_left_running(monkeypatch) -> None:
    """It is the stdio server an assistant is connected through: killing it kills the session
    that asked for the stop, and stops nothing that was running."""
    machine = FakeMachine([proc(200, 1, "uv", "run", "content-factory", "mcp")])
    machine.install(monkeypatch)
    stopper = Stopper(sleep=machine.sleep, monotonic=machine.monotonic)
    stopper.stop_apps()
    assert machine.sent == []
    assert "left running" in stopper.outcomes[-1].result


def test_a_stop_never_signals_itself_or_the_shell_it_was_typed_into(monkeypatch) -> None:
    """The stop runs from inside the repo, under a shell that also runs from inside the repo.
    Both would match every scoping rule; signalling either ends the stop, not the run."""
    me = os.getpid()
    machine = FakeMachine(
        [
            proc(1, 0, "/sbin/init", cwd="/"),
            proc(300, 1, "bash", "-c", "just stop"),
            proc(301, 300, "uv", "run", "content-factory", "stop"),
            proc(me, 301, f"{REPO}/.venv/bin/content-factory", "stop"),
            proc(302, me, "docker", "compose", "down"),
        ]
    )
    machine.install(monkeypatch)
    stopper = Stopper(sleep=machine.sleep, monotonic=machine.monotonic)
    assert "left alone" in stopper.signal_tree(300, machine.processes())
    assert machine.sent == []


def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(monkeypatch) -> None:
    machine = FakeMachine([proc(400, 1, "uv", "run", "content-factory", "worker")])
    machine.stubborn = {400}
    machine.install(monkeypatch)
    stopper = Stopper(grace_s=2.0, sleep=machine.sleep, monotonic=machine.monotonic)
    result = stopper.signal_tree(400, machine.processes())
    assert machine.sent == [(400, signal.SIGTERM), (400, signal.SIGKILL)]
    assert result.startswith("killed")


# -- the registry ----------------------------------------------------------------------------
def test_a_running_run_is_findable_and_a_dead_one_is_pruned(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    with registry.register_run("picture-story", tmp_path / "prj", pid=4242) as handle:
        monkeypatch.setattr(registry, "PID_ALIVE", lambda pid: pid == 4242)
        active = registry.active_runs()
        assert [r.run_key for r in active] == ["picture-story-4242"]
        assert handle.stop_reason() is None
        assert registry.request_stop(active[0], reason="just stop", by="tester")
        assert handle.stop_reason() == "just stop"
        assert "picture-story-4242" in active[0].describe(now=active[0].started_at + 61)

        # A run whose process is gone cannot delete its own file; nobody may report it as running.
        monkeypatch.setattr(registry, "PID_ALIVE", lambda _pid: False)
        assert registry.active_runs() == []
        assert not (tmp_path / "runs" / "picture-story-4242.json").exists()


def test_the_registration_goes_away_when_the_run_ends(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    monkeypatch.setattr(registry, "PID_ALIVE", lambda _pid: True)
    with registry.register_run("hybrid-video", tmp_path / "prj", pid=17) as handle:
        assert handle.path.exists()
    assert registry.active_runs() == []


# -- what a stopped run leaves behind ----------------------------------------------------------
def test_a_stop_between_stages_keeps_the_finished_work_and_says_where_to_resume(
    monkeypatch, tmp_path
) -> None:
    """The point of stopping between stages: everything finished stays finished. The report says
    where it stopped, and that name is what ``--from`` takes to carry on."""
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    from content_factory.config import get_settings
    from content_factory.workflows import stages as stage_module

    get_settings.cache_clear()  # type: ignore[attr-defined]
    ctx = make_context(project_dir=tmp_path / "prj")
    plan_shots = stage_module.STAGE_EXECUTORS[Stage.plan_shots]

    def asked_to_stop_while_it_works(context):
        """Another terminal runs `just stop` while the first stage is executing."""
        for run in registry.active_runs():
            registry.request_stop(run, reason="just stop", by="tester")
        return plan_shots(context)

    monkeypatch.setitem(
        stage_module.STAGE_EXECUTORS, Stage.plan_shots, asked_to_stop_while_it_works
    )
    logs: list[str] = []
    report_path = tmp_path / "run.json"
    try:
        with pytest.raises(RunStopped) as stopped:
            run_plan(
                [("a", Stage.plan_shots, {}), ("b", Stage.route_shots, {})],
                ctx,
                report_path=report_path,
                workflow="picture-story",
                log=logs.append,
            )
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]

    assert stopped.value.at == "b"  # the stage that never started
    on_disk = json.loads(report_path.read_text())
    assert on_disk["stopped"] == {"reason": "just stop", "before": "b"}
    assert [s["stage"] for s in on_disk["stages"]] == ["plan_shots"]
    assert on_disk["stages"][0]["ok"] and on_disk["passed"] is False
    assert any("STOPPED before b" in line for line in logs)


def test_a_stage_that_dies_because_the_stop_killed_its_subprocess_is_not_reported_as_a_failure(
    monkeypatch, tmp_path
) -> None:
    """`FAILED` is what invites a retry. The stage did not break; somebody pulled the plug."""
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    from content_factory.workflows import stages as stage_module

    ctx = make_context(project_dir=tmp_path / "prj")

    def dies(context):
        for run in registry.active_runs():
            registry.request_stop(run, reason="just stop", by="tester")
        msg = "ffmpeg: Terminated"
        raise RuntimeError(msg)

    monkeypatch.setitem(stage_module.STAGE_EXECUTORS, Stage.plan_shots, dies)
    report_path = tmp_path / "run.json"
    with pytest.raises(RunStopped) as stopped:
        run_plan(
            [("a", Stage.plan_shots, {})],
            ctx,
            report_path=report_path,
            workflow="picture-story",
            log=lambda _m: None,
        )
    assert stopped.value.reason == "just stop"
    on_disk = json.loads(report_path.read_text())
    assert on_disk["stopped"]["during"] == "a"
    assert "blocked_at" not in on_disk


# -- the whole button ---------------------------------------------------------------------------
def test_stop_asks_the_run_first_and_then_signals_its_tree(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    machine = FakeMachine(
        [
            proc(500, 1, "uv", "run", "content-factory", "make", "picture-story"),
            proc(501, 500, f"{REPO}/.venv/bin/content-factory", "make", "picture-story"),
            proc(502, 501, "ffmpeg", "-i", "in.mp4"),
        ]
    )
    machine.install(monkeypatch)
    # The run registers the pid it is, which is the CLI under `uv run`, not the launcher.
    with registry.register_run("picture-story", tmp_path / "prj", pid=501):
        stopper = Stopper(sleep=machine.sleep, monotonic=machine.monotonic)
        stopper.stop_local_runs()
        recorded = json.loads((tmp_path / "runs" / "picture-story-501.json").read_text())
    assert recorded["stop_requested"]["reason"] == "stop"
    assert [pid for pid, _sig in machine.sent] == [501, 502]  # the skill subprocess too
    assert [o.result for o in stopper.outcomes] == ["stopped"]  # and `uv` is not a second run


def test_a_run_nobody_registered_still_stops(monkeypatch, tmp_path) -> None:
    """A run started before the registry existed, or by something that does not register, is
    still a run holding the GPU."""
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    machine = FakeMachine([proc(600, 1, "uv", "run", "content-factory", "run-local", "hybrid")])
    machine.install(monkeypatch)
    stopper = Stopper(sleep=machine.sleep, monotonic=machine.monotonic)
    stopper.stop_local_runs()
    assert machine.sent == [(600, signal.SIGTERM)]
    assert "unregistered run" in stopper.outcomes[0].subject


def test_compose_goes_down_with_every_profile(monkeypatch) -> None:
    """Verified against this host with `docker compose down --dry-run`: a plain down lists only
    postgres and temporal, so a container started by `just up search` survives it."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(stop_svc, "SUBPROCESS_RUN", fake_run)
    stopper = Stopper()
    stopper.stop_docker()
    assert calls[0][0] == ["docker", "compose", "--profile", "*", "down"]
    assert calls[0][1]["cwd"] == str(REPO)
    assert stopper.outcomes[-1].result == "down"

    # No docker on the machine is one skipped line, not a crash half way through a stop.
    def missing(cmd, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(stop_svc, "SUBPROCESS_RUN", missing)
    stopper = Stopper()
    stopper.stop_docker()
    assert stopper.outcomes[-1].result.startswith("skipped: FileNotFoundError")


def test_targets_are_independent_and_a_dry_run_touches_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CF_SERVICES_DIR", str(tmp_path))
    machine = FakeMachine([proc(700, 1, "uv", "run", "content-factory", "worker")])
    machine.install(monkeypatch)
    ran: list[list[str]] = []
    monkeypatch.setattr(stop_svc, "SUBPROCESS_RUN", lambda cmd, **kw: ran.append(list(cmd)))
    monkeypatch.setattr(stop_svc, "free_the_gpu", lambda: {"stopped": [], "ollama_unloaded": []})

    outcomes = stop_svc.stop(targets=["apps"])
    assert machine.sent == [(700, signal.SIGTERM)] and ran == []
    assert [o.kind for o in outcomes] == ["worker"]

    with pytest.raises(ValueError, match="unknown stop target"):
        stop_svc.stop(targets=["everything"])


def test_the_command_line_adds_up_the_targets(monkeypatch) -> None:
    """No flags means all four; naming some means only those; `--no-x` subtracts; and naming one
    run means that run, not that run *and* the compose stack."""
    from typer.testing import CliRunner

    from content_factory.cli.main import app

    asked: list[dict] = []
    monkeypatch.setattr(
        stop_svc, "stop", lambda **kwargs: (asked.append(kwargs), [])[1], raising=True
    )
    runner = CliRunner()
    for argv in ([], ["--no-docker"], ["--runs", "--services"], ["--run", "picture-story-42"]):
        assert runner.invoke(app, ["stop", *argv]).exit_code == 0, argv
    assert [a["targets"] for a in asked] == [
        ["runs", "apps", "services", "docker"],
        ["runs", "apps", "services"],
        ["runs", "services"],
        ["runs"],
    ]
    assert asked[-1]["run"] == "picture-story-42"
