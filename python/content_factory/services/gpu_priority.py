"""Hand the card to a higher-priority tenant, and give the render back afterwards.

The flashcards agent (`~/.openclaw/workspace/flashcards-agent`) shares this machine's single 24 GB
card. Its session LLM asks for `OLLAMA_VRAM_GB` plus a 1.5 GB margin -- 17.5 GB by default -- and a
Krea2 anchor render is ~17.4 GB resident, so the two can never be co-resident. Its own
`backend/vram.py` knows only how to unload Ollama models, so when a render holds the card it runs
out of things to free and the study session degrades.

This is the other half: a claim the render side honours.

**Demand, not a clock.** The agent's cron entries at 06:15 and 20:30 send a Web Push nudge; they
touch no GPU. The demand arrives when somebody opens the app. Parking a six-hour film on a
schedule nobody consulted is how the film never finishes, so nothing here is timed -- `yield_gpu`
is called at the moment the VRAM is actually wanted, and returns immediately when there is already
enough free.

**Parking is a stop the run already knows how to survive.** `content-factory stop --runs
--after-stage` writes `stop_requested` into the registration; the runner reads it at the next step
boundary and raises `RunStopped` with its report written and every finished stage left on disk.
The registration's own `step` field is the node it was on, and `run-local --from` takes a node key,
so the resume point needs no parsing of anything. A stage in flight can hold the card for ten
minutes, though, so a deadline escalates to the signalling stop -- still resumable, from the stage
that was interrupted rather than the one after it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from content_factory.runners import registry
from content_factory.services.local import services_dir

MIB_PER_GIB = 1024.0

MAX_CLAIM_HOLD_S = 7200.0
"""After two hours a claim is treated as abandoned rather than binding.

The tenant holding it is a separate program on the other side of a file, and it can crash between
taking the card and giving it back. A claim that never expires would turn one crashed study session
into a machine that refuses to render until somebody notices the file."""


def _query_free_vram_gib() -> float | None:
    try:
        out = subprocess.check_output(
            # Found on PATH, as every other GPU check here and in the flashcards agent's own
            # vram.py does it: pinning /usr/bin would break the driver's alternatives symlink.
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],  # noqa: S607
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return int(out.strip().splitlines()[0]) / MIB_PER_GIB
    except (IndexError, ValueError):
        return None


FREE_VRAM_GIB: Callable[[], float | None] = _query_free_vram_gib
"""Seam: the tests decide how full the card is without owning one.

``None`` means there is no ``nvidia-smi`` to ask. That is not a reason to refuse the other tenant
-- a CPU-only box has nothing to arbitrate -- so every decision below treats it as "no constraint".
"""


@dataclass(frozen=True)
class ParkedRun:
    """A stopped run and the exact command that puts it back."""

    workflow: str
    project_dir: str
    step: str
    argv: list[str]
    run_key: str
    parked_at: float

    def resume_command(self) -> list[str]:
        """The original command with its resume point replaced, not a reconstruction of it.

        A run carries options that decide what it draws -- ``--story``, ``--style``, ``--subject``,
        ``--quality`` -- and a stage after the parking point reads them. Rebuilding the command
        from the workflow name alone would resume a different film.
        """
        argv = list(self.argv) or ["content-factory", "run-local", self.workflow]
        kept: list[str] = []
        drop_value = False
        for arg in argv:
            if drop_value:
                drop_value = False
                continue
            if arg == "--from":
                drop_value = True
                continue
            if arg.startswith("--from="):
                continue
            kept.append(arg)
        if not any(t == "--project-dir" or t.startswith("--project-dir=") for t in kept):
            kept += ["--project-dir", self.project_dir]
        return [*kept, "--from", self.step]


@dataclass(frozen=True)
class Claim:
    """Who holds the card, and what was moved out of the way for them."""

    reason: str
    need_gib: float
    since: float
    parked: list[ParkedRun]

    def held_for(self, now: float) -> float:
        return max(0.0, now - self.since)


def claim_path() -> Path:
    return services_dir() / "gpu-claim.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def read_claim() -> Claim | None:
    try:
        data = json.loads(claim_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    parked = [
        ParkedRun(
            workflow=str(p.get("workflow") or ""),
            project_dir=str(p.get("project_dir") or ""),
            step=str(p.get("step") or ""),
            argv=[str(a) for a in (p.get("argv") or [])],
            run_key=str(p.get("run_key") or ""),
            parked_at=float(p.get("parked_at") or 0.0),
        )
        for p in data.get("parked") or []
        if isinstance(p, dict)
    ]
    return Claim(
        reason=str(data.get("reason") or ""),
        need_gib=float(data.get("need_gib") or 0.0),
        since=float(data.get("since") or 0.0),
        parked=parked,
    )


def _write_claim(claim: Claim) -> None:
    _atomic_write(
        claim_path(),
        {
            "reason": claim.reason,
            "need_gib": claim.need_gib,
            "since": claim.since,
            "parked": [asdict(p) for p in claim.parked],
        },
    )


def clear_claim() -> None:
    claim_path().unlink(missing_ok=True)


@dataclass(frozen=True)
class YieldOutcome:
    ok: bool
    """True when the caller may proceed: enough is free, or nothing could be freed but there is
    nothing here that should stop them trying."""

    free_gib: float | None
    need_gib: float
    parked: list[ParkedRun]
    note: str

    def __str__(self) -> str:
        free = "unknown" if self.free_gib is None else f"{self.free_gib:.1f} GiB"
        return f"{'ok' if self.ok else 'insufficient'}: {self.note} (free {free})"


def yield_gpu(
    *,
    need_gib: float,
    reason: str = "higher-priority tenant",
    deadline_s: float = 90.0,
    poll_s: float = 1.0,
    stop_services: bool = True,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> YieldOutcome:
    """Make room for ``need_gib``, parking the active local run only if that is what it takes.

    Always leaves a claim behind, even when nothing had to move: the claim is also what tells a
    run that is about to start that somebody else is using the card.
    """
    free = FREE_VRAM_GIB()
    started = now()

    if free is None:
        _write_claim(Claim(reason=reason, need_gib=need_gib, since=started, parked=[]))
        return YieldOutcome(True, None, need_gib, [], "no nvidia-smi; nothing to arbitrate")

    if free >= need_gib:
        _write_claim(Claim(reason=reason, need_gib=need_gib, since=started, parked=[]))
        return YieldOutcome(True, free, need_gib, [], "already enough free; nothing stopped")

    runs = registry.active_runs()
    parked = [
        ParkedRun(
            workflow=run.workflow,
            project_dir=run.project_dir,
            step=run.step,
            argv=list(run.argv),
            run_key=run.run_key,
            parked_at=started,
        )
        for run in runs
    ]
    # The claim goes down before anything is stopped. A crash between the stop and the write would
    # otherwise lose the only record of what to restart.
    _write_claim(Claim(reason=reason, need_gib=need_gib, since=started, parked=parked))

    for run in runs:
        registry.request_stop(run, reason=f"{reason} needs the GPU", by="gpu-priority")

    forced = False
    while runs and registry.active_runs():
        if now() - started >= deadline_s:
            from content_factory.services.stop import stop as stop_targets

            # Graceful asking is over. The signalling stop is still resumable -- the runner turns
            # SIGTERM into RunStopped and writes the report -- it just costs the stage in flight.
            stop_targets(
                targets=["runs"],
                graceful=False,
                actor="gpu-priority",
                reason=f"{reason} needs the GPU",
            )
            forced = True
            break
        sleep(poll_s)

    if stop_services:
        # Stopping the run does not unload HiDream, ComfyUI or Ollama, and those are where the
        # gigabytes actually are.
        from content_factory.services.local import free_the_gpu

        free_the_gpu()

    free = FREE_VRAM_GIB()
    ok = free is None or free >= need_gib
    if not parked:
        note = (
            "nothing was running; freed the model servers" if stop_services else "nothing to stop"
        )
    elif forced:
        note = f"stopped {len(parked)} run(s) by signal after {deadline_s:.0f}s"
    else:
        note = f"parked {len(parked)} run(s) at a stage boundary"
    return YieldOutcome(ok, free, need_gib, parked, note)


@dataclass(frozen=True)
class ResumeOutcome:
    resumed: list[ParkedRun]
    commands: list[list[str]]
    note: str


def _spawn_detached(command: Sequence[str], cwd: Path, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("ab")
    try:
        proc = subprocess.Popen(  # noqa: S603 -- the argv is this module's own, not user text
            list(command),
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        handle.close()
    return proc.pid


SPAWN: Callable[[Sequence[str], Path, Path], int] = _spawn_detached
"""Seam: the tests watch what would have been started without starting it."""


def resume_gpu(
    *,
    print_only: bool = False,
    repo_root: Path | None = None,
    stale_after_s: float = 0.0,
    now: Callable[[], float] = time.time,
) -> ResumeOutcome:
    """Put back whatever ``yield_gpu`` parked, and drop the claim.

    Idempotent: with no claim, or a claim that parked nothing, this does nothing and says so. Safe
    to call from the flashcards side's own end-of-session hook and from a timer, because the second
    caller finds nothing left to do.

    ``stale_after_s`` is what makes a timer safe to point at this. The end-of-session hook passes 0
    -- it knows the session is over. A cron safety net for the case where that hook never ran
    (the tenant crashed) passes something longer than a session, so it cannot take the card back
    from somebody who is still studying.
    """
    claim = read_claim()
    if claim is None:
        return ResumeOutcome([], [], "no claim; nothing to resume")
    held = claim.held_for(now())
    if stale_after_s > 0 and held < stale_after_s:
        return ResumeOutcome(
            [], [], f"claim is {int(held)}s old, younger than {int(stale_after_s)}s; left alone"
        )
    if not claim.parked:
        clear_claim()
        return ResumeOutcome([], [], "claim released; no run had been parked")

    root = repo_root or Path(__file__).resolve().parents[3]
    commands = [p.resume_command() for p in claim.parked]
    if print_only:
        return ResumeOutcome(list(claim.parked), commands, "claim held; commands not run")

    started: list[ParkedRun] = []
    for parked, command in zip(claim.parked, commands, strict=True):
        log = services_dir() / "runs" / f"{parked.run_key}.resume.log"
        SPAWN(command, root, log)
        started.append(parked)
    clear_claim()
    return ResumeOutcome(started, commands, f"resumed {len(started)} run(s)")


def blocking_claim(*, max_hold_s: float, now: Callable[[], float] = time.time) -> Claim | None:
    """The claim a starting run should respect, or None.

    A claim older than ``max_hold_s`` is treated as abandoned rather than binding: the tenant that
    took the card is a separate program and can crash, and a stale file must not keep this machine
    from ever rendering again.
    """
    claim = read_claim()
    if claim is None:
        return None
    if claim.held_for(now()) > max_hold_s:
        return None
    return claim
