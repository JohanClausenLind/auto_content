"""Phase-0 spike: a pinned API-format workflow validated, executed against the fixture server,
cancelled, and outputs imported with provenance. No GPU, no network, no real ComfyUI."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import uvicorn

from content_factory.comfyui.client import (
    ComfyUIClient,
    ExecutionState,
    WorkflowValidationError,
    inject_parameters,
    validate_against_object_info,
)
from content_factory.comfyui.fixture_server import OBJECT_INFO, create_app
from content_factory.schemas.comfyui import ComfyWorkflowPackage, ParameterBinding
from content_factory.schemas.fixtures import sample_workflow_package


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def server() -> AsyncIterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        create_app(step_delay_s=0.05), host="127.0.0.1", port=port, log_level="warning"
    )
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    for _ in range(100):
        if srv.started:
            break
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    await task


def test_parameter_injection_is_limited_to_declared_bindings() -> None:
    pkg = sample_workflow_package()
    wf = inject_parameters(pkg, {"width": 32, "height": 16, "color": 0xFF0000})
    assert wf["1"]["inputs"]["width"] == 32
    assert pkg.api_workflow["1"]["inputs"]["width"] == 64  # original untouched
    with pytest.raises(WorkflowValidationError):
        inject_parameters(pkg, {"filename_prefix": "../../etc/passwd"})  # undeclared
    with pytest.raises(WorkflowValidationError):
        inject_parameters(pkg, {"width": 100000})  # above declared maximum
    with pytest.raises(WorkflowValidationError):
        inject_parameters(pkg, {"width": "64"})  # wrong type


def test_validation_rejects_unknown_nodes_unknown_inputs_and_dangling_links() -> None:
    bad = {
        "1": {
            "class_type": "EmptyImage",
            "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0, "evil": 1},
        },
        "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["9", 0]}},
        "3": {"class_type": "RunShell", "inputs": {"cmd": "rm -rf /"}},
    }
    problems = validate_against_object_info(bad, OBJECT_INFO)
    assert any("unknown input 'evil'" in p for p in problems)
    assert any("missing node '9'" in p for p in problems)
    assert any("'RunShell' is not installed" in p for p in problems)
    allow = validate_against_object_info(
        sample_workflow_package().api_workflow, OBJECT_INFO, allowed_node_types={"EmptyImage"}
    )
    assert any("not allowlisted" in p for p in allow)


async def test_execute_and_import_outputs_with_provenance(server: str, tmp_path: Path) -> None:
    client = ComfyUIClient(server, client_id="test-client")
    try:
        events: list[str] = []
        result = await client.run_package(
            sample_workflow_package(),
            {"width": 16, "height": 8, "color": 0x336699},
            output_dir=tmp_path,
            on_progress=lambda ev: events.append(ev.kind),
            timeout_s=10,
        )
    finally:
        await client.aclose()
    assert result.state == ExecutionState.completed
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.local_path.exists() and out.size_bytes > 0
    assert result.provenance is not None
    assert result.provenance.output_sha256 == (out.sha256,)
    assert result.provenance.parameters == {"width": 16, "height": 8, "color": 0x336699}
    assert result.provenance.package_id == "fixture.empty-image"
    assert "execution_start" in events and "executing" in events
    # Determinism: same package + params => same injected-workflow hash and same output bytes.
    client2 = ComfyUIClient(server, client_id="test-client-2")
    try:
        again = await client2.run_package(
            sample_workflow_package(),
            {"width": 16, "height": 8, "color": 0x336699},
            output_dir=tmp_path / "again",
            timeout_s=10,
        )
    finally:
        await client2.aclose()
    assert again.provenance is not None
    assert again.provenance.injected_workflow_sha256 == result.provenance.injected_workflow_sha256
    assert again.provenance.output_sha256 == result.provenance.output_sha256


async def test_cancel_interrupts_a_running_prompt(server: str, tmp_path: Path) -> None:
    slow = ComfyWorkflowPackage(
        package_id="fixture.slow",
        version="0.1.0",
        purpose="cancellation fixture",
        comfyui_min_version="0.3.0",
        api_workflow={
            "1": {
                "class_type": "EmptyImage",
                "inputs": {"width": 8, "height": 8, "batch_size": 1, "color": 0},
            },
            "2": {"class_type": "SlowNode", "inputs": {"images": ["1", 0], "steps": 200}},
            "3": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "slow", "images": ["2", 0]},
            },
        },
        parameters=(
            ParameterBinding(
                name="steps", node_id="2", input_name="steps", kind="int", minimum=1, maximum=10000
            ),
        ),
        expected_outputs=("3",),
    )
    cancel = asyncio.Event()
    progress: list[int] = []

    def on_progress(ev) -> None:
        if ev.kind == "progress" and ev.value is not None:
            progress.append(ev.value)
            if ev.value >= 3:
                cancel.set()

    client = ComfyUIClient(server, client_id="cancel-client")
    try:
        result = await client.run_package(
            slow,
            {"steps": 200},
            output_dir=tmp_path,
            on_progress=on_progress,
            cancel=cancel,
            timeout_s=15,
        )
    finally:
        await client.aclose()
    assert result.state == ExecutionState.cancelled
    assert result.outputs == ()
    assert result.provenance is None
    assert max(progress) < 200


async def test_server_rejects_prompt_with_unknown_node_type(server: str, tmp_path: Path) -> None:
    pkg = ComfyWorkflowPackage(
        package_id="fixture.bad",
        version="0.1.0",
        purpose="server-side rejection",
        comfyui_min_version="0.3.0",
        api_workflow={"1": {"class_type": "RunShell", "inputs": {"cmd": "id"}}},
    )
    client = ComfyUIClient(server)
    try:
        with pytest.raises(WorkflowValidationError):
            await client.run_package(pkg, {}, output_dir=tmp_path, timeout_s=5)
    finally:
        await client.aclose()
