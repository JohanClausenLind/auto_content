"""The SDNQ backend's contract, against a mock transport -- no server, no GPU, no weights."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from content_factory.schemas.sequences import Box, GenerationLock
from content_factory.sequences.diffdiff_map import soft_map
from content_factory.sequences.engine import ControlConditioning, SequenceError
from content_factory.sequences.ideogram_sdnq_backend import Ideogram4SdnqBackend

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def _backend(handler) -> Ideogram4SdnqBackend:
    return Ideogram4SdnqBackend(transport=httpx.MockTransport(handler))


def _lock(**kw) -> GenerationLock:
    base: dict = {
        "workflow_package_id": "ideo_sdnq00001",
        "workflow_package_version": "1.0.0",
        "model_revision": "ideogram-4-sdnq",
        "width": 1024,
        "height": 576,
        "seed": 11,
        "sampler": "euler",
        "steps": 20,
        "guidance": 7.0,
        "style_prompt": "cinematic",
        "camera_prompt": "side view",
        "lighting_prompt": "overcast",
        "background_prompt": "a pavement",
        "reference_asset_sha256": "0" * 64,
    }
    base.update(kw)
    return GenerationLock(**base)


def test_text_to_image_posts_the_caption_and_the_lock() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["path"] = request.url.path
        return httpx.Response(200, json={"png_b64": PNG, "elapsed_s": 41.2, "steps": 20})

    out = _backend(handler).text_to_image('{"high_level_description":"a barn"}', _lock())
    assert out.startswith(b"\x89PNG")
    assert seen["path"] == "/generate"
    assert seen["width"] == 1024 and seen["height"] == 576 and seen["seed"] == 11


def test_inpaint_sends_image_and_map_as_base64() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["path"] = request.url.path
        return httpx.Response(200, json={"png_b64": PNG, "elapsed_s": 39.0, "mode": "inpaint"})

    backend = _backend(handler)
    change_map = soft_map(width=1024, height=576, regenerate=[Box(x=0.1, y=0.1, w=0.3, h=0.3)])
    out = backend.inpaint(
        b"original-png-bytes",
        change_map,
        '{"high_level_description":"a barn"}',
        width=1024,
        height=576,
        seed=5,
    )

    assert out.startswith(b"\x89PNG")
    assert seen["path"] == "/inpaint"
    assert base64.b64decode(seen["image_b64"]) == b"original-png-bytes"
    assert base64.b64decode(seen["diffdiff_map_b64"]) == change_map
    assert "denoising_start" not in seen, "omitted rather than sent as null when unset"
    # The server's own timing, not the stage's wall clock, is what the run should record.
    assert backend.last_facts["elapsed_s"] == 39.0


def test_reference_edit_still_refuses_and_says_where_to_go() -> None:
    """Differential diffusion repaints ONE picture; it cannot hold a world across frames."""
    backend = _backend(lambda r: httpx.Response(200, json={"png_b64": PNG}))
    lock = _lock()
    with pytest.raises(SequenceError, match="Flux2ReferenceBackend"):
        backend.edit(b"anchor", b"control", "turn her head", lock, attempt=0)
    with pytest.raises(SequenceError, match="inpaint"):
        backend.edit_conditioned(b"anchor", ControlConditioning(), "turn her head", lock, attempt=0)


def test_generate_refuses_conditioning_it_cannot_honour() -> None:
    """Silently dropping references would bill a frame that ignored what it was conditioned on."""
    backend = _backend(lambda r: httpx.Response(200, json={"png_b64": PNG}))
    conditioning = ControlConditioning(reference_pngs=(b"ref",))
    with pytest.raises(SequenceError, match="no image path into its conditioning"):
        backend.generate("{}", conditioning, _lock(), seed=3)


def test_server_error_names_the_endpoint_and_the_status() -> None:
    backend = _backend(lambda r: httpx.Response(500, text="CUDA out of memory"))
    with pytest.raises(SequenceError, match=r"500.*CUDA out of memory"):
        backend.text_to_image("{}", _lock())


def test_ready_requires_loaded_not_merely_listening() -> None:
    """The server answers /healthz while still loading 18 GB; 'up' is not 'ready'."""
    loading = _backend(lambda r: httpx.Response(200, json={"loaded": False}))
    assert loading.ready() is False
    live = _backend(lambda r: httpx.Response(200, json={"loaded": True}))
    assert live.ready() is True
