"""Phase-5 gate: one media skill runs through both a ComfyUI fixture and a mock cloud backend
unchanged (same request, same invocation path, equivalent verified results)."""

from __future__ import annotations

import base64
import io
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from PIL import Image

from content_factory.comfyui.fixture_server import create_app
from content_factory.media.generate import (
    ComfyUIMediaBackend,
    GeneratedMediaRequest,
    MediaGenerationError,
    MockCloudMediaBackend,
    run_media_skill,
)
from content_factory.schemas.fixtures import sample_workflow_package

REQUEST = GeneratedMediaRequest(
    request_id="gmr_test00000001",
    purpose="editorial illustration (test)",
    prompt="flat teal square",
    width=64,
    height=64,
    seed=7,
)


def _png(width: int, height: int, seed: int) -> bytes:
    img = Image.new(
        "RGB", (width, height), ((seed * 37) % 255, (seed * 91) % 255, (seed * 13) % 255)
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def cloud_backend() -> MockCloudMediaBackend:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "image_base64": base64.b64encode(
                    _png(body["width"], body["height"], body["seed"])
                ).decode(),
                "model": "mock-model-1",
                "id": "req-1",
            },
        )

    return MockCloudMediaBackend(transport=httpx.MockTransport(handler))


def test_same_request_runs_through_mock_cloud(tmp_path: Path) -> None:
    out = run_media_skill(REQUEST, cloud_backend(), workdir=tmp_path)
    assert out.result.backend == "mock-cloud"
    assert out.result.width == 64 and out.result.height == 64
    assert out.result.provenance["prompt"] == REQUEST.prompt


def test_wrong_dimensions_fail_verification(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"image_base64": base64.b64encode(_png(10, 10, 1)).decode()}
        )

    bad = MockCloudMediaBackend(transport=httpx.MockTransport(handler))
    with pytest.raises(MediaGenerationError, match="requested 64x64"):
        run_media_skill(REQUEST, bad, workdir=tmp_path)


def test_same_request_runs_through_comfyui_fixture_unchanged(tmp_path: Path) -> None:
    import threading
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        pkg = sample_workflow_package()
        backend = ComfyUIMediaBackend(
            f"http://127.0.0.1:{port}", pkg, param_map={"width": "width", "height": "height"}
        )
        out = run_media_skill(REQUEST, backend, workdir=tmp_path)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert out.result.backend == "comfyui"
    assert out.result.width == 64 and out.result.height == 64
    assert out.result.model_revision == "fixture.empty-image@0.1.0"
    assert out.result.provenance["workflow_sha256"]
    # Equivalent, verified outputs from both backends via the identical invocation path.
    cloud = run_media_skill(REQUEST, cloud_backend(), workdir=tmp_path)
    for res in (out.result, cloud.result):
        assert res.request_id == REQUEST.request_id and res.seed == REQUEST.seed
