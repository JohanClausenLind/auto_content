"""HiDream backend: loopback-only HTTP contract, per-attempt seed variation, clear failures."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from content_factory.schemas.sequences import GenerationLock
from content_factory.sequences.engine import SequenceError
from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend

LOCK = GenerationLock(
    workflow_package_id="hidream-o1-image",
    workflow_package_version="1.0.0",
    model_revision="HiDream-O1-Image-Dev",
    width=1024,
    height=1024,
    seed=32,
    sampler="flow_match",
    steps=28,
    guidance=5.0,
    style_prompt="photorealistic",
    camera_prompt="medium shot",
    lighting_prompt="golden hour",
    background_prompt="forest path",
    reference_asset_sha256="0" * 64,
)
PNG = b"\x89PNG fake"


def _backend(handler) -> HiDreamReferenceEditBackend:
    return HiDreamReferenceEditBackend(transport=httpx.MockTransport(handler))


def test_edit_sends_anchor_and_varies_seed_per_attempt() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"png_b64": base64.b64encode(PNG).decode(), "elapsed_s": 1})

    backend = _backend(handler)
    out = backend.edit(b"anchor-bytes", b"control", "move the hands closer", LOCK, attempt=1)
    backend.edit(b"anchor-bytes", b"control", "move the hands closer", LOCK, attempt=2)
    assert out == PNG
    assert base64.b64decode(calls[0]["ref_image_b64"]) == b"anchor-bytes"
    assert calls[0]["scheduler"] == "flow_match" and calls[0]["steps"] == 28
    assert (calls[0]["seed"], calls[1]["seed"]) == (32, 33)  # regen attempts truly re-roll


def test_text_to_image_has_no_reference() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"png_b64": base64.b64encode(PNG).decode()})

    _backend(handler).text_to_image("two people on a path", LOCK)
    assert "ref_image_b64" not in calls[0] and calls[0]["scheduler"] is None


def test_server_down_raises_sequence_error_with_start_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(SequenceError, match="skills/image/hidream"):
        _backend(handler).edit(b"a", b"c", "x", LOCK, attempt=1)


def test_server_error_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="CUDA out of memory")

    with pytest.raises(SequenceError, match="CUDA out of memory"):
        _backend(handler).edit(b"a", b"c", "x", LOCK, attempt=1)
