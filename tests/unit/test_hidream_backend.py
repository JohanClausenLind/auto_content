"""HiDream backend: loopback-only HTTP contract, per-attempt seed variation, clear failures."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from content_factory.schemas.sequences import Box, GenerationLock
from content_factory.sequences.engine import ControlConditioning, SequenceError
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
    assert [base64.b64decode(r) for r in calls[0]["ref_images_b64"]] == [
        b"anchor-bytes",
        b"control",
    ]
    assert (
        calls[0]["scheduler"] is None and calls[0]["steps"] == 28
    )  # two refs: no editing scheduler
    assert calls[0]["guidance_scale"] == 5.0 and "layout_bboxes" not in calls[0]
    assert (calls[0]["seed"], calls[1]["seed"]) == (32, 33)  # regen attempts truly re-roll


def test_text_to_image_has_no_reference() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"png_b64": base64.b64encode(PNG).decode()})

    _backend(handler).text_to_image("two people on a path", LOCK)
    assert "ref_images_b64" not in calls[0] and calls[0]["scheduler"] is None


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


def _recording_backend() -> tuple[HiDreamReferenceEditBackend, list[dict]]:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"png_b64": base64.b64encode(PNG).decode()})

    return _backend(handler), calls


def test_edit_conditioned_sends_anchor_then_refs_then_control_with_boxes() -> None:
    backend, calls = _recording_backend()
    cond = ControlConditioning(
        control_png=b"control",
        reference_pngs=(b"identity", b"rough", b"skeleton"),
        layout_boxes=(Box(x=0.4, y=0.3, w=0.2, h=0.6),),
    )
    backend.edit_conditioned(b"anchor", cond, "hold the pose", LOCK, attempt=2)
    refs = [base64.b64decode(r) for r in calls[0]["ref_images_b64"]]
    assert refs == [b"anchor", b"identity", b"rough", b"skeleton", b"control"]
    assert calls[0]["layout_bboxes"] == [[0.4, 0.3, 0.2, 0.6]]
    assert calls[0]["seed"] == 33 and calls[0]["scheduler"] is None


def test_control_reference_can_be_turned_off() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"png_b64": base64.b64encode(PNG).decode()})

    backend = HiDreamReferenceEditBackend(
        transport=httpx.MockTransport(handler), send_control_as_reference=False
    )
    backend.edit(b"anchor", b"control", "x", LOCK, attempt=1)
    assert [base64.b64decode(r) for r in calls[0]["ref_images_b64"]] == [b"anchor"]
    assert calls[0]["scheduler"] == "flow_match"  # single reference: editing scheduler applies


def test_generate_uses_refs_and_boxes_without_an_anchor() -> None:
    backend, calls = _recording_backend()
    cond = ControlConditioning(
        reference_pngs=(b"identity", b"rough"), layout_boxes=(Box(x=0.1, y=0.1, w=0.3, h=0.5),)
    )
    out = backend.generate("a man by a bench", cond, LOCK, seed=99)
    assert out == PNG
    assert [base64.b64decode(r) for r in calls[0]["ref_images_b64"]] == [b"identity", b"rough"]
    assert calls[0]["layout_bboxes"] == [[0.1, 0.1, 0.3, 0.5]] and calls[0]["seed"] == 99
    assert calls[0]["prompt"] == "a man by a bench"
