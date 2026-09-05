"""One-click downloads: allowlist validation, idempotency, comfy-cli envelope parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.comfyui.downloads import (
    DownloadManager,
    DownloadValidationError,
    ModelDownloadRequest,
    RunnerResult,
    validate_request,
)

GOOD = ModelDownloadRequest(
    url="https://huggingface.co/acme/pack/resolve/main/style.safetensors",
    relative_path="models/loras",
    filename="style.safetensors",
)


def envelope(ok: bool, data: dict | None = None, error: str | None = None) -> str:
    return json.dumps(
        {"schema": "envelope/1", "type": "envelope", "ok": ok, "data": data, "error": error}
    )


class FakeRunner:
    def __init__(self, responses: list[RunnerResult]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> RunnerResult:
        self.calls.append(args)
        return self.responses.pop(0)


def test_validation_refuses_bad_sources() -> None:
    for url in (
        "http://huggingface.co/x",  # not https
        "https://evil.example/model.safetensors",  # host not allowlisted
        "https://nothuggingface.co/x",  # suffix trick
    ):
        with pytest.raises(DownloadValidationError):
            validate_request(GOOD.model_copy(update={"url": url}))
    with pytest.raises(ValueError):
        ModelDownloadRequest(url=GOOD.url, relative_path="models/../etc", filename="x.safetensors")
    with pytest.raises(ValueError):
        ModelDownloadRequest(url=GOOD.url, relative_path="models/loras", filename="../x")


def test_start_runs_comfy_in_background_and_polls_status(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            RunnerResult(0, envelope(True, {"download_id": "dl_1", "status": "running"}), ""),
            RunnerResult(0, envelope(True, {"id": "dl_1", "status": "running"}), ""),
            RunnerResult(0, envelope(True, {"id": "dl_1", "status": "complete"}), ""),
        ]
    )
    manager = DownloadManager(runner=runner, poll_floor_s=0.0)
    job = manager.start(GOOD, tmp_path)
    assert job.state == "running" and job.comfy_download_id == "dl_1"
    start_args = runner.calls[0]
    assert start_args[:5] == ["comfy", "--skip-prompt", "--json", "model", "download"]
    assert "--background" in start_args
    assert str(tmp_path / "models" / "loras") in start_args

    assert manager.jobs()[0].state == "running"  # first poll: still running
    assert manager.jobs()[0].state == "complete"  # second poll: finished
    assert runner.calls[1][:5] == ["comfy", "--skip-prompt", "--json", "model", "download-status"]


def test_start_is_idempotent_and_existing_files_skip_the_network(tmp_path: Path) -> None:
    dest = tmp_path / "models" / "loras"
    dest.mkdir(parents=True)
    (dest / "style.safetensors").write_bytes(b"weights")
    runner = FakeRunner([])  # any call would pop from an empty list and fail the test
    manager = DownloadManager(runner=runner)
    job = manager.start(GOOD, tmp_path)
    assert job.state == "already_installed"
    assert manager.start(GOOD, tmp_path) is job
    assert runner.calls == []


def test_failed_launch_is_reported_with_the_error(tmp_path: Path) -> None:
    runner = FakeRunner([RunnerResult(1, envelope(False, None, "quota exceeded"), "boom")])
    manager = DownloadManager(runner=runner)
    job = manager.start(GOOD, tmp_path)
    assert job.state == "failed"
    assert "quota exceeded" in job.detail


def test_running_job_completes_when_the_file_lands(tmp_path: Path) -> None:
    runner = FakeRunner([RunnerResult(0, envelope(True, {"download_id": "dl_9"}), "")])
    manager = DownloadManager(runner=runner, poll_floor_s=0.0)
    job = manager.start(GOOD, tmp_path)
    assert job.state == "running"
    dest = tmp_path / "models" / "loras"
    dest.mkdir(parents=True)
    (dest / "style.safetensors").write_bytes(b"weights")
    assert manager.jobs()[0].state == "complete"  # file presence wins; no status call needed
