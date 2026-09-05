"""Stop everything this repo started. ``just stop`` is this module with no arguments.

Five kinds of thing run in the background on this machine, started by five different commands,
and until now each was stopped its own way: the workflow itself (a local run holding the GPU, or a
durable run inside Temporal), the Temporal worker, the API and web dev servers, the GPU servers
the stages start for themselves (HiDream, ComfyUI, and Ollama's resident models), and the compose
stack. Stopping "everything" meant remembering all five, in the right order, and finding pids by
hand for the ones nothing tracked.

Order matters and is fixed here: the runs first (they are what is using the rest), then the
processes that serve them, then the GPU tenants, then compose. Every step is independent and
survives the others failing — a stop button that gives up half way because Postgres was already
down is not a stop button.

Nothing outside this checkout is ever signalled. A process qualifies only when it is running from
this repository (its cwd is inside it, or the path is in its argv) *and* its argv names one of the
commands below. That is deliberately narrower than "everything with the word content-factory in
it": this host runs other projects, other checkouts and the operator's own shells.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from content_factory.runners import registry
from content_factory.services.local import REPO_ROOT, TENANTS, LocalServices, free_the_gpu

SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
SIGNAL: Callable[[int, int], None] = os.kill

TARGETS: tuple[str, ...] = ("runs", "apps", "services", "docker")
"""What a stop can act on, in the order it acts."""

# argv[0]-style command name -> what that process is. A subcommand this table does not name is
# not a background program (``stop`` and ``doctor`` are the commands that ask about them).
CLI_COMMANDS: dict[str, str] = {
    "worker": "worker",
    "serve": "api",
    "make": "run",
    "run-local": "run",
    "demo": "run",
    "mcp": "mcp",
}
KIND_LABELS: dict[str, str] = {
    "run": "local run",
    "worker": "Temporal worker",
    "api": "API server",
    "web": "web dev server",
    "mcp": "MCP server",
}
STOPPABLE_KINDS: tuple[str, ...] = ("worker", "api", "web")
"""What ``--apps`` stops. ``run`` has its own registry-driven path; ``mcp`` is left alone — it is
the stdio server an assistant is connected through, so killing it kills the session that asked."""


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    args: tuple[str, ...]
    cwd: str | None


@dataclass(frozen=True)
class Outcome:
    """One line of what happened, for the operator and for the tests."""

    kind: str
    subject: str
    result: str

    def __str__(self) -> str:
        return f"{self.kind:<8} {self.subject:<34} {self.result}"


def _read_processes() -> list[ProcInfo]:
    """Every process this user can see, from /proc. Linux only, which this control plane is."""
    found: list[ProcInfo] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            stat = (entry / "stat").read_text()
        except OSError:
            continue  # exited between listing and reading, or not ours to read
        args = tuple(a for a in raw.decode("utf-8", "replace").split("\0") if a)
        if not args:
            continue  # kernel thread
        try:
            # comm can contain spaces and parens, so ppid is read after the last ')'.
            ppid = int(stat[stat.rindex(")") + 1 :].split()[1])
        except (ValueError, IndexError):
            continue
        try:
            cwd: str | None = os.readlink(entry / "cwd")
        except OSError:
            cwd = None
        found.append(ProcInfo(int(entry.name), ppid, args, cwd))
    return found


PROCESSES: Callable[[], list[ProcInfo]] = _read_processes
"""Seam: the tests describe a machine instead of having one."""


def classify(proc: ProcInfo, *, repo_root: Path = REPO_ROOT) -> str | None:
    """What this process is to us, or None when it is none of our business.

    Matching is on argv entries, not on a joined command line: ``grep "content-factory make"``
    contains every word and must never be mistaken for a run.
    """
    root = str(repo_root)
    if not (
        (proc.cwd or "") == root
        or (proc.cwd or "").startswith(root + os.sep)
        or any(root in a for a in proc.args)
    ):
        return None
    for index, arg in enumerate(proc.args):
        if PurePath(arg).name in {"content-factory", "content_factory"}:
            command = next((a for a in proc.args[index + 1 :] if not a.startswith("-")), "")
            return CLI_COMMANDS.get(command)
    if any(
        "content_factory.workflows.worker" in a or a.endswith("apps/workers/main.py")
        for a in proc.args
    ):
        return "worker"  # `python apps/workers/main.py`, and the `-c "…worker import main"` form
    if any("apps.api.main" in a or "content_factory.api.app" in a for a in proc.args):
        return "api"  # `uvicorn apps.api.main:app`, the entry point apps/api documents
    if any(PurePath(a).name in {"vite", "vite.js"} for a in proc.args) or (
        "@content-factory/web" in proc.args
    ):
        return "web"
    return None


def _ancestors(pid: int, by_pid: dict[int, ProcInfo]) -> set[int]:
    """This process and everything that spawned it. Never signalled: a stop that kills the shell
    it was typed into (or the agent that asked for it) has not stopped anything, it has crashed."""
    chain: set[int] = set()
    current = pid
    while current > 1 and current not in chain:
        chain.add(current)
        parent = by_pid.get(current)
        if parent is None:
            break
        current = parent.ppid
    return chain


def _descendants(pid: int, procs: Iterable[ProcInfo], *, skip: Iterable[int] = ()) -> list[int]:
    """Everything under ``pid``, not descending through ``skip``.

    Skipping matters when the tree being stopped contains this very process — a run and the stop
    that ends it are often children of the same shell. Walking through the protected chain would
    collect the stop's own children (the ``docker compose down`` it is about to run) as things to
    signal.
    """
    children: dict[int, list[int]] = {}
    for proc in procs:
        children.setdefault(proc.ppid, []).append(proc.pid)
    blocked = set(skip)
    out: list[int] = []
    queue = [pid]
    while queue:
        for child in children.get(queue.pop(), []):
            if child != pid and child not in out and child not in blocked:
                out.append(child)
                queue.append(child)
    return out


def _outermost(matched: Sequence[ProcInfo], procs: Sequence[ProcInfo]) -> list[ProcInfo]:
    """Drop matches that are already inside another match's tree.

    ``uv run content-factory worker`` is two processes that are both the worker; signalling the
    tree of the outer one takes the inner one with it, and reporting both would claim two stops.
    """
    by_pid = {p.pid: p for p in procs}
    pids = {p.pid for p in matched}
    return sorted(
        (p for p in matched if not _ancestors(p.ppid, by_pid) & pids), key=lambda p: p.pid
    )


class Stopper:
    """One stop, start to finish. Constructed fresh per invocation; nothing here is reusable
    state, and every seam it touches (processes, signals, subprocesses, the clock) is injectable
    so the tests can describe a busy machine without having one."""

    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        grace_s: float = 10.0,
        dry_run: bool = False,
        actor: str = "operator",
        reason: str = "stop",
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo_root = repo_root
        self.grace_s = grace_s
        self.dry_run = dry_run
        self.actor = actor
        self.reason = reason
        self._sleep = sleep
        self._monotonic = monotonic
        self.outcomes: list[Outcome] = []

    # -- reporting ------------------------------------------------------------------------------
    def record(self, kind: str, subject: str, result: str) -> Outcome:
        outcome = Outcome(kind, subject, result)
        self.outcomes.append(outcome)
        return outcome

    # -- processes ------------------------------------------------------------------------------
    def _protected(self, procs: Sequence[ProcInfo]) -> set[int]:
        by_pid = {p.pid: p for p in procs}
        return _ancestors(os.getpid(), by_pid)

    def signal_tree(self, pid: int, procs: Sequence[ProcInfo]) -> str:
        """SIGTERM the process and everything under it, then SIGKILL whatever is left.

        The whole tree, because the interesting processes are launchers: ``uv run`` around the
        CLI, the CLI around a skill's own venv, that skill around ffmpeg. Terminating only the pid
        in the registry leaves the GPU exactly as busy as it was.
        """
        protected = self._protected(procs)
        if pid in protected:
            return "left alone (this stop is running inside it)"
        pids = [p for p in [pid, *_descendants(pid, procs, skip=protected)] if p not in protected]
        if not pids:
            return "already gone"
        if self.dry_run:
            return f"would signal {len(pids)} process(es)"
        for target in pids:
            self._send(target, signal.SIGTERM)
        deadline = self._monotonic() + self.grace_s
        while self._monotonic() < deadline:
            pids = [p for p in pids if registry.PID_ALIVE(p)]
            if not pids:
                return "stopped"
            self._sleep(0.2)
        for target in pids:
            self._send(target, signal.SIGKILL)
        return f"killed ({len(pids)} did not stop in {self.grace_s:.0f}s)"

    def _send(self, pid: int, sig: int) -> None:
        try:
            SIGNAL(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    # -- targets --------------------------------------------------------------------------------
    def stop_local_runs(self, *, only: str = "", graceful: bool = False) -> None:
        """Ask every registered local run to stop, then (unless graceful) signal its tree.

        The request is written first either way: a run that survives the signal — one whose stage
        swallowed it, one started under a supervisor — must not begin another stage.

        A run that never registered (a process from before this existed, or one started some other
        way) is still a run holding the GPU, so the process table is swept for those too. Only
        when no single run was named: a name is a registry key, and matching it against argv would
        make ``--run`` mean two different things.
        """
        procs = PROCESSES()
        runs = [r for r in registry.active_runs() if not only or r.run_key == only]
        acted = False
        covered: set[int] = set()
        for run in runs:
            covered.update([run.pid, *_descendants(run.pid, procs)])
            if not self.dry_run:
                registry.request_stop(run, reason=self.reason, by=self.actor)
            acted = True
            if graceful:
                asked = "would ask to stop" if self.dry_run else "will stop"
                self.record("run", run.describe(), f"{asked} at the end of the current stage")
                continue
            self.record("run", run.describe(), self.signal_tree(run.pid, procs))
        # A registered run is usually the *middle* of its tree: `uv run` launched it and it
        # launched a skill. Neither the launcher above it nor anything below it is a second run.
        strays = (
            []
            if only
            else _outermost(
                [
                    p
                    for p in self._of_kind(procs, ("run",))
                    if p.pid not in covered and not covered & set(_descendants(p.pid, procs))
                ],
                procs,
            )
        )
        for proc in strays:
            acted = True
            subject = f"unregistered run (pid {proc.pid})"
            if graceful:
                self.record("run", subject, "skipped: nothing to ask, it keeps no registration")
                continue
            self.record("run", subject, self.signal_tree(proc.pid, procs))
        if not acted:
            self.record("run", only or "none", "no local run is executing")

    def _of_kind(self, procs: Sequence[ProcInfo], kinds: Sequence[str]) -> list[ProcInfo]:
        return [p for p in procs if classify(p, repo_root=self.repo_root) in kinds]

    def stop_apps(self, *, kinds: Sequence[str] = STOPPABLE_KINDS) -> None:
        """Stop the long-lived processes this repo starts: worker, API, web dev server."""
        procs = PROCESSES()
        serving = _outermost(self._of_kind(procs, kinds), procs)
        if not serving:
            self.record("apps", "none", "nothing of ours is serving")
        for proc in serving:
            kind = classify(proc, repo_root=self.repo_root) or "app"
            label = f"{KIND_LABELS.get(kind, kind)} (pid {proc.pid})"
            self.record(kind, label, self.signal_tree(proc.pid, procs))
        for proc in _outermost(self._of_kind(procs, ("mcp",)), procs):
            self.record(
                "mcp",
                f"MCP server (pid {proc.pid})",
                "left running (an assistant is connected through it)",
            )

    def stop_gpu_services(self) -> None:
        """HiDream, ComfyUI and Ollama's resident models: the three tenants of the card."""
        if self.dry_run:
            with LocalServices() as services:
                for tenant in TENANTS:
                    up = services.responding(tenant)
                    self.record("gpu", tenant, "would stop" if up else "not running")
            return
        try:
            result = free_the_gpu()
        except Exception as exc:  # a tenant that will not die must not take the rest with it
            self.record("gpu", "hidream, comfyui", f"skipped: {_one_line(exc)}")
            return
        stopped = list(result.get("stopped") or [])  # type: ignore[arg-type]
        unloaded = list(result.get("ollama_unloaded") or [])  # type: ignore[arg-type]
        for tenant in stopped:
            self.record("gpu", str(tenant), "stopped")
        for model in unloaded:
            self.record("gpu", f"ollama {model}", "unloaded")
        if not stopped and not unloaded:
            self.record("gpu", "none", "no GPU tenant was up")

    def stop_docker(self) -> None:
        """``docker compose --profile "*" down``. Without the wildcard compose only touches the
        profiles it was given, so a container started by ``just up search`` (or s3, or notify)
        survives the stop — which is why ``just down`` passes it now as well."""
        if self.dry_run:
            self.record("docker", "compose", 'would run: docker compose --profile "*" down')
            return
        try:
            proc = SUBPROCESS_RUN(
                ["docker", "compose", "--profile", "*", "down"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # no docker, or it never answered
            self.record("docker", "compose", f"skipped: {_one_line(exc)}")
            return
        if proc.returncode == 0:
            self.record("docker", "compose", "down")
        else:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            self.record("docker", "compose", f"skipped: {tail[-1] if tail else 'docker failed'}")

    def stop_durable_runs(self, *, only: str = "", graceful: bool = False) -> None:
        """Cancel the runs Temporal still has open. Skipped, with the reason, when the engine or
        the database is not there — which is the normal case on a box whose compose is down."""
        if self.dry_run:
            self.record("durable", only or "every running workflow", "would stop through Temporal")
            return
        try:
            results = asyncio.run(_stop_durable(only, self.actor, self.reason, graceful))
        except Exception as exc:  # every engine failure is one skipped line, not a crash
            self.record("durable", only or "runs", f"skipped: {_one_line(exc)}")
            return
        if not results:
            self.record("durable", "none", "no run is executing")
        for result in results:
            detail = str(result.get("outcome", ""))
            corrected = result.get("nodes_corrected")
            if corrected:
                detail += f" ({corrected} node(s) marked stopped)"
            self.record("durable", str(result.get("run_id", "")), detail)


async def _stop_durable(only: str, actor: str, reason: str, graceful: bool) -> list[dict]:
    """Stop the open durable runs at once, not one after another.

    A stop waits a few seconds for each run to close itself before cancelling it, and a dev
    Temporal accumulates runs whose worker is long gone — this host had 31 of them. In sequence
    that is eight minutes of a stop button doing nothing visible; together it is one wait.
    """
    from content_factory.services.runs import open_run_ids, stop_run

    run_ids = [only] if only else await asyncio.wait_for(open_run_ids(), timeout=20)
    results = await asyncio.gather(
        *(stop_run(run_id, actor=actor, reason=reason, graceful=graceful) for run_id in run_ids),
        return_exceptions=True,
    )
    return [
        {"run_id": run_id, "outcome": f"skipped: {_one_line(result)}"}
        if isinstance(result, BaseException)
        else result
        for run_id, result in zip(run_ids, results, strict=True)
    ]


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def stop(
    *,
    targets: Sequence[str] = TARGETS,
    run: str = "",
    graceful: bool = False,
    grace_s: float = 10.0,
    dry_run: bool = False,
    actor: str = "operator",
    reason: str = "stop",
) -> list[Outcome]:
    """Stop the named targets and return one line per thing acted on.

    ``run`` narrows to a single run — a local run key or a durable run id, whichever it matches.
    """
    unknown = [t for t in targets if t not in TARGETS]
    if unknown:
        msg = f"unknown stop target(s) {unknown}; known: {list(TARGETS)}"
        raise ValueError(msg)
    stopper = Stopper(grace_s=grace_s, dry_run=dry_run, actor=actor, reason=reason)
    if "runs" in targets and run:
        # A name is either a local run key or a durable run id. Asking both would report the other
        # one as missing every time, and "not found" next to "stopped" reads like a failure.
        if any(r.run_key == run for r in registry.active_runs()):
            stopper.stop_local_runs(only=run, graceful=graceful)
        else:
            stopper.stop_durable_runs(only=run, graceful=graceful)
    elif "runs" in targets:
        stopper.stop_durable_runs(graceful=graceful)
        stopper.stop_local_runs(graceful=graceful)
    if "apps" in targets:
        stopper.stop_apps()
    if "services" in targets:
        stopper.stop_gpu_services()
    if "docker" in targets:
        stopper.stop_docker()
    return stopper.outcomes
