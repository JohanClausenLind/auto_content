"""Live HiDream server: one conditioned anchor (two references + one layout box). Needs the skill
server running with the model loaded; skipped otherwise."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image, ImageDraw

from content_factory.schemas.sequences import Box, GenerationLock
from content_factory.sequences.engine import ControlConditioning
from content_factory.sequences.hidream_backend import DEFAULT_ENDPOINT, HiDreamReferenceEditBackend

pytestmark = pytest.mark.live


def _server_ready() -> bool:
    try:
        r = httpx.get(f"{DEFAULT_ENDPOINT}/healthz", timeout=2.0)
        return r.status_code == 200 and bool(r.json().get("loaded"))
    except httpx.HTTPError:
        return False


def _png(colour: tuple[int, int, int], size: tuple[int, int] = (512, 512)) -> bytes:
    img = Image.new("RGB", size, (30, 30, 34))
    d = ImageDraw.Draw(img)
    d.rectangle([size[0] // 3, size[1] // 5, 2 * size[0] // 3, size[1] - 20], fill=colour)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.skipif(not _server_ready(), reason="hidream server not running / model not loaded")
def test_conditioned_anchor_round_trip() -> None:
    backend = HiDreamReferenceEditBackend(timeout_s=1200)
    lock = GenerationLock(
        workflow_package_id="hidream-o1-image",
        workflow_package_version="1.0.0",
        model_revision="HiDream-O1-Image",
        width=1024,
        height=576,
        seed=7,
        sampler="default",
        steps=20,
        guidance=5.0,
        style_prompt="clay render, studio light",
        camera_prompt="medium shot",
        lighting_prompt="soft key light",
        background_prompt="grey studio",
        reference_asset_sha256="0" * 64,
    )
    cond = ControlConditioning(
        reference_pngs=(_png((200, 160, 140)), _png((90, 90, 90))),
        layout_boxes=(Box(x=0.4, y=0.2, w=0.2, h=0.7),),
    )
    png = backend.generate("a person standing in a grey studio, full body", cond, lock, seed=7)
    img = Image.open(io.BytesIO(png))
    assert img.size == (1024, 576)
