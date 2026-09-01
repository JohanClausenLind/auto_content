"""HiDream-O1-Image reference-edit backend for the sequence engine.

Talks to the loopback skill server (skills/image/hidream/server.py) — the control plane never
imports torch or the model code. Every frame is an edit of the ANCHOR (hub-and-spoke), with the
lock's seed varied per attempt so a drift-failed frame genuinely regenerates."""

from __future__ import annotations

import base64

import httpx

from content_factory.schemas.sequences import GenerationLock
from content_factory.sequences.engine import ReferenceEditBackend, SequenceError

DEFAULT_ENDPOINT = "http://127.0.0.1:8801"


class HiDreamReferenceEditBackend(ReferenceEditBackend):
    name = "hidream-o1"

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_s: float = 900.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(base_url=endpoint, timeout=timeout_s, transport=transport)

    def ready(self) -> bool:
        try:
            r = self._http.get("/healthz")
            return r.status_code == 200 and bool(r.json().get("loaded"))
        except httpx.HTTPError:
            return False

    def text_to_image(self, prompt: str, lock: GenerationLock) -> bytes:
        """Generate the sequence ANCHOR from text (no reference)."""
        return self._call(prompt, ref_png=None, lock=lock, seed=lock.seed)

    def edit(
        self,
        anchor_png: bytes,
        control_png: bytes,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        # The control PNG stays in the cache-marker hash (layout identity); the model itself is
        # conditioned by the anchor reference plus the compiled instruction text.
        del control_png
        return self._call(instruction, ref_png=anchor_png, lock=lock, seed=lock.seed + attempt - 1)

    def _call(
        self, prompt: str, *, ref_png: bytes | None, lock: GenerationLock, seed: int
    ) -> bytes:
        body: dict = {
            "prompt": prompt,
            "width": lock.width,
            "height": lock.height,
            "seed": seed,
            "steps": lock.steps,
            "scheduler": lock.sampler if ref_png is not None else None,
        }
        if ref_png is not None:
            body["ref_image_b64"] = base64.b64encode(ref_png).decode()
        try:
            r = self._http.post("/generate", json=body)
        except httpx.HTTPError as exc:
            raise SequenceError(
                f"hidream server unreachable at {self._http.base_url} — start it with "
                "`uv run --project skills/image/hidream python skills/image/hidream/server.py`"
            ) from exc
        if r.status_code != 200:
            raise SequenceError(f"hidream server error {r.status_code}: {r.text[:300]}")
        return base64.b64decode(r.json()["png_b64"])
