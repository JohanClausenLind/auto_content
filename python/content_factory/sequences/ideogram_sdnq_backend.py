"""Ideogram 4 via the SDNQ loopback server: text-to-image, plus the inpaint ComfyUI cannot do."""

from __future__ import annotations

import base64

import httpx

from content_factory.schemas.sequences import GenerationLock
from content_factory.sequences.engine import (
    ControlConditioning,
    ReferenceEditBackend,
    SequenceError,
)

DEFAULT_ENDPOINT = "http://127.0.0.1:8802"

_NO_EDIT = (
    "ideogram4-sdnq cannot reference-edit: the model has no image path into its conditioning, so "
    "it cannot hold a world across frames. For a region of ONE picture use inpaint(); to compose "
    "a new frame from an existing one use Flux2ReferenceBackend."
)


class Ideogram4SdnqBackend(ReferenceEditBackend):
    """One structured JSON caption to one frame, and one change map to a repaint."""

    name = "ideogram4-sdnq"
    send_control_as_reference = False

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        preset: str = "default",
        timeout_s: float = 1800.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.preset = preset
        self._http = httpx.Client(base_url=endpoint, timeout=timeout_s, transport=transport)
        self.last_facts: dict[str, object] = {}
        """What the server reported about the last frame: elapsed_s, steps, mode. The stage's own
        wall clock includes model load, so this is the only honest per-frame number."""

    def ready(self) -> bool:
        try:
            r = self._http.get("/healthz")
            return r.status_code == 200 and bool(r.json().get("loaded"))
        except httpx.HTTPError:
            return False

    def text_to_image(self, prompt: str, lock: GenerationLock) -> bytes:
        return self._post(
            "/generate",
            {
                "prompt": prompt,
                "width": lock.width,
                "height": lock.height,
                "seed": lock.seed,
                "preset": self.preset,
            },
        )

    def generate(
        self, prompt: str, conditioning: ControlConditioning, lock: GenerationLock, *, seed: int
    ) -> bytes:
        if not conditioning.empty:
            raise SequenceError(_NO_EDIT)
        return self._post(
            "/generate",
            {
                "prompt": prompt,
                "width": lock.width,
                "height": lock.height,
                "seed": seed,
                "preset": self.preset,
            },
        )

    def inpaint(
        self,
        image_png: bytes,
        change_map_png: bytes,
        prompt: str,
        *,
        width: int,
        height: int,
        seed: int,
        denoising_start: float | None = None,
    ) -> bytes:
        """Repaint where the map is dark; `prompt` describes the FINISHED frame, not the hole."""
        body: dict[str, object] = {
            "prompt": prompt,
            "image_b64": base64.b64encode(image_png).decode(),
            "diffdiff_map_b64": base64.b64encode(change_map_png).decode(),
            "width": width,
            "height": height,
            "seed": seed,
            "preset": self.preset,
        }
        if denoising_start is not None:
            body["denoising_start"] = denoising_start
        return self._post("/inpaint", body)

    def edit(
        self,
        anchor_png: bytes,
        control_png: bytes,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        raise SequenceError(_NO_EDIT)

    def edit_conditioned(
        self,
        anchor_png: bytes,
        conditioning: ControlConditioning,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        raise SequenceError(_NO_EDIT)

    # --- internals --------------------------------------------------------------------------

    def _post(self, path: str, body: dict) -> bytes:
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            msg = (
                f"{self.name}: server at {self.endpoint} did not answer ({exc}). Start it with "
                "`uv run content-factory services up ideogram4`."
            )
            raise SequenceError(msg) from exc
        if response.status_code != 200:
            detail = response.text[:400]
            msg = f"{self.name}: {path} returned {response.status_code}: {detail}"
            raise SequenceError(msg)
        payload = response.json()
        png_b64 = payload.get("png_b64")
        if not png_b64:
            msg = f"{self.name}: {path} returned no image: {payload}"
            raise SequenceError(msg)
        self.last_facts = {k: v for k, v in payload.items() if k != "png_b64"}
        return base64.b64decode(png_b64)
