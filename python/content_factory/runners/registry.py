"""Which local runs are executing right now, recorded on disk so another terminal can stop one.

A local run (``content-factory make``, ``run-local``) is one long foreground process that holds
the GPU for minutes at a time, and nothing outside its own terminal knew it existed: stopping it
meant finding the pid by hand and hoping the skill subprocess it had spawned died with it. Every
run now writes ``<repo>/.services/runs/<run key>.json`` while it executes — pid, workflow, project
dir, the step it is on — and removes the file when it ends.

``content-factory stop`` reads that directory and writes ``stop_requested`` into the file. The
runner reads it back at the next step boundary and raises :class:`RunStopped`, so the run stops
between stages with its report written and its artifacts intact, and ``--from`` resumes there.
Stopping *now* (the default) also signals the process tree, because a single video stage can hold
the card for ten minutes and "stop" has to mean stop.

Registrations are advisory. A run that is killed outright cannot delete its own file, so a file
whose pid is gone is stale: readers ignore it and prune it.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from content_factory.services.local import services_dir

_SAFE = re.compile(r"[^a-z0-9]+")


def _pid_alive(pid: int) -> bool:
    """Signal 0 asks the kernel about a pid without touching it. ``PermissionError`` means the
    process exists and belongs to somebody else, which is still alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


PID_ALIVE: Callable[[int], bool] = _pid_alive
"""Seam: the tests decide who is alive without spawning processes."""


class RunStopped(RuntimeError):  # noqa: N818 - an operator decision, not an error
    """A run ended because somebody asked it to, not because anything failed.

    Kept apart from ``LocalRunError`` so callers can tell an operator's decision from a defect:
    a stopped run is resumable and prints no traceback; a failed one is a bug or a bad input.
    """

    def __init__(self, reason: str, *, at: str, report: dict | None = None) -> None:
        super().__init__(f"stopped at {at}: {reason}" if at else f"stopped: {reason}")
        self.reason = reason
        self.at = at
        self.report: dict = report or {}


def runs_dir() -> Path:
    return services_dir() / "runs"


def run_key(workflow: str, pid: int) -> str:
    """A name an operator can type: the workflow, then the pid that makes it unique."""
    return f"{_SAFE.sub('-', workflow.lower()).strip('-') or 'run'}-{pid}"


@dataclass(frozen=True)
class ActiveRun:
    """One registration file, as read. ``stopped_reason`` is set once somebody has asked."""

    run_key: str
    pid: int
    workflow: str
    project_dir: str
    started_at: float
    step: str
    stopped_reason: str | None
    path: Path
    argv: list[str]
    """The command that started this run, so something that stops it can start the same one again.

    A run's options decide what it draws (``--story``, ``--style``, ``--subject``, ``--quality``);
    rebuilding a resume command from the workflow name alone would quietly resume a different
    film. Empty for a run registered by a caller that had no argv to give, e.g. a test."""

    @property
    def alive(self) -> bool:
        return PID_ALIVE(self.pid)

    def describe(self, now: float | None = None) -> str:
        elapsed = int((now if now is not None else time.time()) - self.started_at)
        where = f" at {self.step}" if self.step else ""
        return f"{self.run_key} (pid {self.pid}, {elapsed // 60}m{elapsed % 60:02d}s{where})"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _as_active(path: Path, data: dict) -> ActiveRun | None:
    try:
        pid = int(data["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    stop = data.get("stop_requested") or None
    return ActiveRun(
        run_key=str(data.get("run_key") or path.stem),
        pid=pid,
        workflow=str(data.get("workflow") or ""),
        project_dir=str(data.get("project_dir") or ""),
        started_at=float(data.get("started_at") or 0.0),
        step=str(data.get("step") or ""),
        stopped_reason=str(stop.get("reason", "")) if isinstance(stop, dict) else None,
        path=path,
        argv=[str(a) for a in (data.get("argv") or [])],
    )


def active_runs(*, prune: bool = True) -> list[ActiveRun]:
    """Every run whose process is still alive, oldest first. Stale files are deleted on the way
    past: a killed run cannot clean up after itself, and a stop command that reported a run which
    died yesterday would be lying."""
    found: list[ActiveRun] = []
    directory = runs_dir()
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        data = _read(path)
        run = _as_active(path, data) if data else None
        if run is None or not run.alive:
            if prune:
                path.unlink(missing_ok=True)
            continue
        found.append(run)
    return sorted(found, key=lambda r: r.started_at)


def request_stop(run: ActiveRun, *, reason: str, by: str = "operator") -> bool:
    """Ask one run to stop at its next step boundary. False when the file is already gone."""
    data = _read(run.path)
    if data is None:
        return False
    data["stop_requested"] = {"at": time.time(), "by": by, "reason": reason}
    _atomic_write(run.path, data)
    return True


class RunHandle:
    """The live registration, held by the runner for the length of a run."""

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.run_key = key
        self.step = ""

    def note(self, **fields: object) -> None:
        """Record what the run is doing now (the step name). Best effort: a run must not fail
        because its own bookkeeping file went missing."""
        data = _read(self.path)
        if data is None:
            return
        data.update(fields)
        if "step" in fields:
            self.step = str(fields["step"])
        _atomic_write(self.path, data)

    def stop_reason(self) -> str | None:
        """The reason somebody gave, or None. Read from disk every time — the request arrives
        from another process, so a cached answer is no answer."""
        data = _read(self.path)
        stop = (data or {}).get("stop_requested")
        if not isinstance(stop, dict):
            return None
        return str(stop.get("reason") or "") or "stop requested"

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


@contextmanager
def _sigterm_stops_the_run(handle: RunHandle) -> Iterator[None]:
    """Turn the SIGTERM a stop sends into :class:`RunStopped` inside the run.

    Python's default action for SIGTERM is to die on the spot, which loses the report and tells
    the operator nothing about why the run ended. Raising instead lets the runner write "stopped"
    into ``run.json`` and exit with a sentence. Only ever installed on the main thread of the main
    interpreter — ``signal.signal`` raises anywhere else, and a worker thread must not steal the
    process's signal handling from whoever owns it.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _stop(_signum: int, _frame: object) -> None:
        raise RunStopped(handle.stop_reason() or "SIGTERM", at=handle.step)

    try:
        previous = signal.signal(signal.SIGTERM, _stop)
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def register_run(
    workflow: str,
    project_dir: Path,
    *,
    pid: int | None = None,
    catch_sigterm: bool = True,
    argv: Sequence[str] | None = None,
) -> Iterator[RunHandle]:
    """Publish this run for the length of the block, and take the registration down after.

    ``argv`` defaults to this process's own command line, which is what makes a parked run
    resumable as the run it actually was rather than as its workflow's defaults.
    """
    pid = pid if pid is not None else os.getpid()
    key = run_key(workflow, pid)
    path = runs_dir() / f"{key}.json"
    _atomic_write(
        path,
        {
            "run_key": key,
            "pid": pid,
            "workflow": workflow,
            "project_dir": str(project_dir),
            "started_at": time.time(),
            "step": "",
            "argv": list(argv) if argv is not None else list(sys.argv),
        },
    )
    handle = RunHandle(path, key)
    try:
        if catch_sigterm:
            with _sigterm_stops_the_run(handle):
                yield handle
        else:
            yield handle
    finally:
        handle.release()
