"""Ideogram 4 text-to-image backend for the sequence engine, driven through local ComfyUI.

Deliberately **text-to-image only**. The other two backends here compose reference images --
HiDream takes one, FLUX.2 chains up to six -- and Ideogram 4 as shipped takes none: its graph is
two transformers behind a ``DualModelGuider`` with no image path into the conditioning. Rather
than fake an edit by re-prompting and pretending the result is the same picture, ``edit`` and
``edit_conditioned`` refuse and say what the caller should reach for instead. A lane that needs a
frame to be the *same world* as the frame before it cannot be served by this model today.

That refusal is the useful part of the contract: the sequence engine is hub-and-spoke precisely
because drift has to be measured against something, and a backend that cannot see the anchor has
nothing to hold. Use it for the first frame, then compose from that frame with FLUX.2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from content_factory.comfyui.client import ComfyUIClient, ExecutionState
from content_factory.media.ideogram_packages import DEFAULT_PRESET, ideogram4_package
from content_factory.schemas.sequences import GenerationLock
from content_factory.sequences.engine import (
    ControlConditioning,
    ReferenceEditBackend,
    SequenceError,
)

DEFAULT_ENDPOINT = "http://127.0.0.1:8188"

_NO_EDIT = (
    "ideogram4 is text-to-image only: its graph has no image path into the conditioning, so it "
    "cannot be shown an anchor and cannot hold a world across frames. Make the first frame here, "
    "then compose the rest from it with Flux2ReferenceBackend."
)


class Ideogram4Backend(ReferenceEditBackend):
    """One structured JSON caption to one frame.

    ``workdir`` is where collected outputs land; it must be a real directory because ComfyUI is
    handed file paths, not bytes.
    """

    name = "ideogram4"
    send_control_as_reference = False

    def __init__(
        self,
        *,
        workdir: Path,
        endpoint: str = DEFAULT_ENDPOINT,
        preset: str = DEFAULT_PRESET,
        timeout_s: float = 1800.0,
    ) -> None:
        self.workdir = workdir
        self.endpoint = endpoint.rstrip("/")
        self.preset = preset
        self.timeout_s = timeout_s

    def text_to_image(self, prompt: str, lock: GenerationLock) -> bytes:
        return self._run(prompt, lock=lock, seed=lock.seed)

    def generate(
        self, prompt: str, conditioning: ControlConditioning, lock: GenerationLock, *, seed: int
    ) -> bytes:
        if not conditioning.empty:
            raise SequenceError(_NO_EDIT)
        return self._run(prompt, lock=lock, seed=seed)

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

    def ready(self) -> bool:
        async def _probe() -> bool:
            client = ComfyUIClient(self.endpoint, client_id="ideogram4-probe")
            try:
                return bool(await client.object_info())
            except Exception:
                return False
            finally:
                await client.aclose()

        try:
            return asyncio.run(_probe())
        except Exception:
            return False

    # --- internals --------------------------------------------------------------------------

    def _run(self, prompt: str, *, lock: GenerationLock, seed: int) -> bytes:
        package = ideogram4_package(width=lock.width, height=lock.height, preset=self.preset)
        params: dict[str, int | float | str] = {
            "prompt": prompt,
            "width": lock.width,
            "height": lock.height,
            "seed": seed,
        }

        async def _go() -> bytes:
            self.workdir.mkdir(parents=True, exist_ok=True)
            client = ComfyUIClient(self.endpoint, client_id=f"ideogram4-{seed}")
            try:
                result = await client.run_package(
                    package,
                    params,
                    output_dir=self.workdir / "comfy",
                    timeout_s=self.timeout_s,
                    # Polling, not the websocket, for the reason flux2_backend records: ComfyUI
                    # runs here with --cache-none and goes quiet long enough to look like a hang.
                    collect="history",
                )
                if result.state != ExecutionState.completed or not result.outputs:
                    msg = f"{self.name}: comfyui run {result.state}: {result.error}"
                    raise SequenceError(msg)
                return result.outputs[0].local_path.read_bytes()
            finally:
                await client.aclose()

        try:
            return asyncio.run(_go())
        except SequenceError:
            raise
        except Exception as exc:
            msg = (
                f"{self.name}: ComfyUI at {self.endpoint} failed this frame ({exc}). Start it with "
                "`just up` or check that the Ideogram 4 weights are visible in its models/ tree."
            )
            raise SequenceError(msg) from exc
