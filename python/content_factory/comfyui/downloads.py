"""One-click model downloads through comfy-cli.

The only thing this module ever executes is the allowlisted `comfy model download` command
(https URLs on approved hosts, safe filenames, destination pinned inside the workspace's
models/ tree — the same rules as models/install.py). Transfers run detached in comfy-cli's own
background worker (`--background`); state is refreshed by polling `comfy model download-status`
when jobs are read, so this process holds no threads. Everything is idempotent per destination:
starting the same file twice returns the same job, and a file already on disk is reported
`already_installed` without touching the network.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import Field

from content_factory.comfyui.inventory import comfy_cli_default_workspace
from content_factory.config import Settings
from content_factory.models.install import ModelInstallError, validate_model_source
from content_factory.schemas.base import SchemaModel

JobState = Literal["running", "complete", "failed", "already_installed"]

FILENAME_PATTERN = r"^[A-Za-z0-9._ ()-]+$"
RELATIVE_PATH_PATTERN = r"^models/[a-z_]+$"


class DownloadValidationError(Exception):
    pass


class ModelDownloadRequest(SchemaModel):
    url: str = Field(min_length=1)
    relative_path: str = Field(pattern=RELATIVE_PATH_PATTERN)
    filename: str = Field(min_length=1, max_length=255, pattern=FILENAME_PATTERN)


def validate_request(req: ModelDownloadRequest) -> None:
    try:
        validate_model_source(req.url, req.filename)
    except ModelInstallError as err:
        raise DownloadValidationError(str(err)) from err
    if req.filename.startswith("."):
        raise DownloadValidationError("invalid filename")


def download_workspace(settings: Settings) -> Path:
    """Where downloads land: comfy-cli's own workspace when it manages ComfyUI, else ours."""
    if settings.comfyui.managed_by_comfy_cli:
        cli_workspace = comfy_cli_default_workspace()
        if cli_workspace is not None:
            return cli_workspace
    return Path(settings.comfyui.workspace).expanduser()


@dataclass
class DownloadJob:
    job_id: str
    url: str
    relative_path: str
    filename: str
    state: JobState
    detail: str = ""
    comfy_download_id: str | None = None
    started_at: float = field(default_factory=time.time)
    # When this job was last refreshed; jobs() skips re-polling within the manager's floor so
    # client poll frequency cannot translate 1:1 into comfy-cli subprocess spawns.
    last_polled_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "relative_path": self.relative_path,
            "filename": self.filename,
            "state": self.state,
            "detail": self.detail,
            "started_at": self.started_at,
        }


# Runs one comfy invocation; injected so tests never spawn processes.
Runner = Callable[[list[str]], "RunnerResult"]


@dataclass(frozen=True)
class RunnerResult:
    returncode: int
    stdout: str
    stderr: str


def default_runner(args: list[str]) -> RunnerResult:
    completed = subprocess.run(  # noqa: S603 — fixed argv built from validated fields
        args, capture_output=True, text=True, timeout=120, check=False
    )
    return RunnerResult(completed.returncode, completed.stdout, completed.stderr)


def _envelope(stdout: str) -> dict[str, Any]:
    """comfy --json prints one envelope object; NDJSON streams end with it."""
    for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


_DONE_WORDS = frozenset({"complete", "completed", "done", "finished", "success"})
_FAIL_WORDS = frozenset({"failed", "error", "cancelled", "canceled"})


def _status_word(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("status", "state"):
            value = data.get(key)
            if isinstance(value, str):
                return value.lower()
    return ""


class DownloadManager:
    """Jobs keyed by destination path. Poll-on-read; no threads of its own."""

    def __init__(self, runner: Runner = default_runner, poll_floor_s: float = 2.0) -> None:
        self._runner = runner
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = Lock()
        self._poll_floor_s = poll_floor_s

    def start(self, req: ModelDownloadRequest, workspace: Path) -> DownloadJob:
        validate_request(req)
        dest_dir = (workspace / req.relative_path).resolve()
        if not dest_dir.is_relative_to(workspace.resolve()):
            raise DownloadValidationError("destination escapes the workspace")
        dest = dest_dir / req.filename
        key = str(dest)
        with self._lock:
            existing = self._jobs.get(key)
            if existing and existing.state in ("running", "complete", "already_installed"):
                return existing
            # A zero-byte file is a leftover from a failed transfer, never an installed model.
            if dest.exists() and dest.stat().st_size > 0:
                job = DownloadJob(
                    key, req.url, req.relative_path, req.filename, "already_installed"
                )
                self._jobs[key] = job
                return job

            result = self._runner(
                [
                    "comfy",
                    "--skip-prompt",
                    "--json",
                    "model",
                    "download",
                    "--url",
                    req.url,
                    "--relative-path",
                    str(dest_dir),
                    "--filename",
                    req.filename,
                    "--background",
                ]
            )
            envelope = _envelope(result.stdout)
            data = envelope.get("data")
            download_id = None
            if isinstance(data, dict):
                for id_key in ("download_id", "id"):
                    value = data.get(id_key)
                    if isinstance(value, str | int):
                        download_id = str(value)
                        break
            if result.returncode != 0 or envelope.get("ok") is False:
                error = str(envelope.get("error") or result.stderr.strip()[-500:] or "comfy failed")
                job = DownloadJob(
                    key, req.url, req.relative_path, req.filename, "failed", detail=error
                )
            else:
                job = DownloadJob(
                    key,
                    req.url,
                    req.relative_path,
                    req.filename,
                    "running",
                    detail="transfer started",
                    comfy_download_id=download_id,
                )
            self._jobs[key] = job
            return job

    def _refresh(self, job: DownloadJob) -> None:
        if job.state != "running":
            return
        dest = Path(job.job_id)
        if dest.exists() and dest.stat().st_size > 0:
            job.state = "complete"
            job.detail = "file present"
            return
        if job.comfy_download_id is None:
            return  # nothing to poll; completion shows up when the file lands
        result = self._runner(
            ["comfy", "--skip-prompt", "--json", "model", "download-status", job.comfy_download_id]
        )
        envelope = _envelope(result.stdout)
        word = _status_word(envelope.get("data"))
        if word in _DONE_WORDS:
            job.state = "complete"
            job.detail = "download finished"
        elif word in _FAIL_WORDS or envelope.get("ok") is False:
            job.state = "failed"
            job.detail = str(envelope.get("error") or word or "download failed")
        elif word:
            job.detail = word

    def jobs(self) -> list[DownloadJob]:
        now = time.time()
        with self._lock:
            to_poll = [
                job
                for job in self._jobs.values()
                if job.state == "running" and now - job.last_polled_at >= self._poll_floor_s
            ]
            for job in to_poll:
                job.last_polled_at = now
        # Refresh OUTSIDE the lock: each poll can shell out to comfy-cli (up to its timeout),
        # and holding the lock across that would park start() — and every other reader —
        # behind a hung status call.
        for job in to_poll:
            self._refresh(job)
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
