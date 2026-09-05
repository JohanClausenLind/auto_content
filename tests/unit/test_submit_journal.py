"""A submitted prompt is written down, and a rerun adopts it instead of queueing a second one.

The failure this closes: `run_package` submitted a prompt and kept the id **only in memory**. A
process that died after the POST — a Ctrl-C, an OOM, a worker restart — left no record of the job
it had queued, so a rerun submitted the identical workflow again. On the LTX GGUF stack that is
another forty seconds of exclusive GPU, and if the first prompt is still running it is two prompts
competing for a card that fits one.

Runs against the fixture ComfyUI server: no GPU, no real ComfyUI, no network.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import uvicorn

from content_factory.comfyui.client import ComfyUIClient, ExecutionState
from content_factory.comfyui.fixture_server import create_app
from content_factory.schemas.fixtures import sample_workflow_package

PARAMS: dict[str, int | float | str] = {"width": 32, "height": 16}


def _read(path: Path) -> dict:
    """Synchronous file access, out of the async body: ruff's ASYNC240 is right that a blocking
    read inside a coroutine is a smell, and a helper says "this is deliberate, and it is a test"."""
    return json.loads(path.read_text())


def _write(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record))


def _write_text(path: Path, text: str) -> None:
    path.write_text(text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def server() -> AsyncIterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        create_app(step_delay_s=0.02), host="127.0.0.1", port=port, log_level="warning"
    )
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    for _ in range(200):
        if srv.started:
            break
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    await task


async def _run(endpoint: str, tmp_path: Path, journal: Path | None, params=None):
    client = ComfyUIClient(endpoint, client_id="journal-test")
    try:
        return await client.run_package(
            sample_workflow_package(),
            params or PARAMS,
            output_dir=tmp_path / "out",
            collect="history",
            timeout_s=30.0,
            journal=journal,
        )
    finally:
        await client.aclose()


async def test_the_prompt_id_is_written_down_and_cleared_on_success(
    server: str, tmp_path: Path
) -> None:
    journal = tmp_path / "submitted.json"
    result = await _run(server, tmp_path, journal)
    assert result.state == ExecutionState.completed
    # Cleared, because the prompt has an outcome: leaving it would make the next run adopt a
    # finished prompt and re-import the same outputs for a workflow that may have changed.
    assert not journal.exists()


async def test_a_rerun_adopts_the_prompt_instead_of_submitting_a_second_one(
    server: str, tmp_path: Path
) -> None:
    """The whole point. The journal is written by hand here to stand for the run that died."""
    from content_factory.comfyui.client import inject_parameters

    client = ComfyUIClient(server, client_id="journal-test")
    try:
        package = sample_workflow_package()
        workflow = inject_parameters(package, PARAMS)
        prompt_id, _ = await client.submit(workflow)
        journal = tmp_path / "submitted.json"
        client._record_submitted(journal, prompt_id, package, workflow)
        record = _read(journal)
        assert record["prompt_id"] == prompt_id
        assert record["package_id"] == package.package_id
        assert record["endpoint"] == server
        # Wait for the fixture server to finish it, then a rerun must adopt rather than resubmit.
        await client._poll_history(prompt_id, timeout_s=30.0, poll_interval_s=0.05, cancel=None)
        adopted = await client._adopt_submitted(journal, workflow)
        assert adopted == prompt_id
    finally:
        await client.aclose()


async def test_a_journal_for_a_different_workflow_is_not_adopted(
    server: str, tmp_path: Path
) -> None:
    """A rerun that changed one parameter is a different job, and adopting the old prompt would
    hand back a clip made from the parameter the operator just changed."""
    from content_factory.comfyui.client import inject_parameters

    client = ComfyUIClient(server, client_id="journal-test")
    try:
        package = sample_workflow_package()
        first = inject_parameters(package, PARAMS)
        prompt_id, _ = await client.submit(first)
        journal = tmp_path / "submitted.json"
        client._record_submitted(journal, prompt_id, package, first)
        changed = inject_parameters(package, {"width": 48, "height": 16})
        assert await client._adopt_submitted(journal, changed) is None
    finally:
        await client.aclose()


async def test_a_prompt_the_server_has_never_heard_of_is_not_adopted(
    server: str, tmp_path: Path
) -> None:
    """ComfyUI restarted: the history and the queue are both empty, so the id is worthless and the
    only correct move is to submit. Adopting it would wait out the timeout for nothing."""
    from content_factory.comfyui.client import inject_parameters

    client = ComfyUIClient(server, client_id="journal-test")
    try:
        package = sample_workflow_package()
        workflow = inject_parameters(package, PARAMS)
        journal = tmp_path / "submitted.json"
        client._record_submitted(journal, "0" * 32, package, workflow)
        assert await client._adopt_submitted(journal, workflow) is None
    finally:
        await client.aclose()


async def test_a_journal_from_another_endpoint_is_not_adopted(server: str, tmp_path: Path) -> None:
    """A prompt id is only meaningful to the server that issued it."""
    from content_factory.comfyui.client import inject_parameters

    client = ComfyUIClient(server, client_id="journal-test")
    try:
        package = sample_workflow_package()
        workflow = inject_parameters(package, PARAMS)
        journal = tmp_path / "submitted.json"
        client._record_submitted(journal, "abc", package, workflow)
        record = _read(journal)
        record["endpoint"] = "http://somewhere-else:8188"
        _write(journal, record)
        assert await client._adopt_submitted(journal, workflow) is None
    finally:
        await client.aclose()


async def test_an_unreadable_journal_is_ignored_rather_than_fatal(
    server: str, tmp_path: Path
) -> None:
    """A half-written journal must not stop a run. It is a cache, not a contract."""
    from content_factory.comfyui.client import inject_parameters

    client = ComfyUIClient(server, client_id="journal-test")
    try:
        workflow = inject_parameters(sample_workflow_package(), PARAMS)
        journal = tmp_path / "submitted.json"
        _write_text(journal, "{not json")
        assert await client._adopt_submitted(journal, workflow) is None
        assert await client._adopt_submitted(None, workflow) is None
    finally:
        await client.aclose()


async def test_running_with_no_journal_still_works(server: str, tmp_path: Path) -> None:
    """Every existing caller passed nothing, and must keep working unchanged."""
    result = await _run(server, tmp_path, None)
    assert result.state == ExecutionState.completed
