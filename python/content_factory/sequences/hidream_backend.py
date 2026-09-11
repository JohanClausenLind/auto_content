"""HiDream-O1-Image reference-edit backend for the sequence engine.

Talks to the loopback skill server (skills/image/hidream/server.py) — the control plane never
imports torch or the model code. Every frame is an edit of the ANCHOR (hub-and-spoke), with the
lock's seed varied per attempt so a drift-failed frame genuinely regenerates.

Conditioning goes to the model the way the upstream pipeline supports it: extra reference images
(identity references first, then the Blender rough render and the OpenPose skeleton) and
``layout_bboxes`` placing each identity reference. The legacy 2D control raster is sent as one more
reference when ``send_control_as_reference`` is on -- it is OFF by default, because the raster
`run_sequence` compiles is a pure ``#FF0000`` rectangle on black and the model draws what it is
shown (see ``ImageSequenceSettings.control_as_reference`` for the measurement). It is part of the
cache key either way."""

from __future__ import annotations

import base64
from collections.abc import Sequence

import httpx

from content_factory.schemas.sequences import Box, GenerationLock
from content_factory.sequences.engine import (
    ControlConditioning,
    ReferenceEditBackend,
    SequenceError,
)

DEFAULT_ENDPOINT = "http://127.0.0.1:8801"


class HiDreamReferenceEditBackend(ReferenceEditBackend):
    name = "hidream-o1"

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_s: float = 900.0,
        transport: httpx.BaseTransport | None = None,
        send_control_as_reference: bool = False,
    ) -> None:
        self._http = httpx.Client(base_url=endpoint, timeout=timeout_s, transport=transport)
        self.endpoint = endpoint
        """Kept for provenance: with a pool of servers the run has to be able to say which host
        made which frame, and ``base_url`` on the client is not readable back as the caller wrote
        it."""
        self.send_control_as_reference = send_control_as_reference
        self.last_facts: dict[str, object] = {}
        """What the server said about the frame it just made. The server has reported ``elapsed_s``
        since it was written and nothing read it, so the only timing anyone had was the stage's own
        wall clock — which is how a 6 min 25 s per-anchor figure went three months without being
        compared against the 114 s the server reported for the same request (STATUS 3238)."""

    def ready(self) -> bool:
        try:
            r = self._http.get("/healthz")
            return r.status_code == 200 and bool(r.json().get("loaded"))
        except httpx.HTTPError:
            return False

    def text_to_image(self, prompt: str, lock: GenerationLock) -> bytes:
        """Generate the sequence ANCHOR from text (no reference)."""
        return self._call(prompt, ref_pngs=(), lock=lock, seed=lock.seed)

    def generate(
        self, prompt: str, conditioning: ControlConditioning, lock: GenerationLock, *, seed: int
    ) -> bytes:
        """Anchor from text plus conditioning: identity refs + layout boxes + structural refs."""
        return self._call(
            prompt,
            ref_pngs=conditioning.reference_pngs,
            layout_boxes=conditioning.layout_boxes,
            lock=lock,
            seed=seed,
        )

    def edit(
        self,
        anchor_png: bytes,
        control_png: bytes,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        return self.edit_conditioned(
            anchor_png,
            ControlConditioning(control_png=control_png),
            instruction,
            lock,
            attempt=attempt,
        )

    def edit_conditioned(
        self,
        anchor_png: bytes,
        conditioning: ControlConditioning,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        refs: tuple[bytes, ...] = (anchor_png, *conditioning.reference_pngs)
        if self.send_control_as_reference and conditioning.control_png:
            refs = (*refs, conditioning.control_png)
        return self._call(
            instruction,
            ref_pngs=refs,
            layout_boxes=conditioning.layout_boxes,
            lock=lock,
            seed=lock.seed + attempt - 1,
        )

    def _call(
        self,
        prompt: str,
        *,
        ref_pngs: Sequence[bytes],
        layout_boxes: Sequence[Box] = (),
        lock: GenerationLock,
        seed: int,
    ) -> bytes:
        body: dict = {
            "prompt": prompt,
            "width": lock.width,
            "height": lock.height,
            "seed": seed,
            "steps": lock.steps,
            "guidance_scale": lock.guidance,
            # the editing scheduler only applies to single-reference edits (dev model)
            "scheduler": lock.sampler if len(ref_pngs) == 1 else None,
        }
        if ref_pngs:
            body["ref_images_b64"] = [base64.b64encode(r).decode() for r in ref_pngs]
        if layout_boxes:
            body["layout_bboxes"] = [[b.x, b.y, b.w, b.h] for b in layout_boxes]
        try:
            r = self._http.post("/generate", json=body)
        except httpx.HTTPError as exc:
            raise SequenceError(
                f"hidream server unreachable at {self._http.base_url} — start it with "
                "`uv run --project skills/image/hidream python skills/image/hidream/server.py`"
            ) from exc
        if r.status_code != 200:
            raise SequenceError(f"hidream server error {r.status_code}: {r.text[:300]}")
        payload = r.json()
        self.last_facts = {
            k: payload[k] for k in ("elapsed_s", "refs", "layout_boxes") if k in payload
        }
        return base64.b64decode(payload["png_b64"])
