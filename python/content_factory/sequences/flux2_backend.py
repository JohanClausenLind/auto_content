"""FLUX.2-dev multi-reference backend for the sequence engine, driven through local ComfyUI.

The reason this exists beside ``hidream_backend`` is measured rather than architectural. Over
thirty staged runner anchors HiDream-O1 held the *world* — one track across every frame,
consecutive-frame churn 9.6 to 19.8 against a threshold of 42 — and did not hold the *character*:
four outfit combinations, race bibs appearing with different numbers, two frames reading as a
different person. A single reference is a pose hint. Identity needs several references composed at
once, which is what FLUX.2-dev's chained ``ReferenceLatent`` conditioning does and what this
backend reaches.

It talks to ComfyUI rather than to a bespoke server, because that is how LTX-2.5 already runs here:
one governed ``ComfyWorkflowPackage`` per graph shape, parameters injected only through declared
bindings, and every image uploaded as a file rather than inlined. The control plane never imports
torch.

Reference ORDER is the contract, and it is the caller's to keep. Every reference arrives through
the same node type, so the graph cannot tell an identity sheet from a depth pass —
``reference_roles`` records what each slot was meant to be so a run's provenance says which sheet
was in which slot, and a later frame can be conditioned the same way.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path

from content_factory.comfyui.client import ComfyUIClient, ExecutionState
from content_factory.media.flux2_packages import (
    MAX_REFERENCES,
    flux2_reference_package,
    reference_param_name,
)
from content_factory.schemas.comfyui import ComfyWorkflowPackage
from content_factory.schemas.sequences import GenerationLock
from content_factory.sequences.engine import (
    ControlConditioning,
    ReferenceEditBackend,
    SequenceError,
)

DEFAULT_ENDPOINT = "http://127.0.0.1:8188"


class Flux2ReferenceBackend(ReferenceEditBackend):
    """Text plus up to six reference images to one frame, composed by FLUX.2-dev.

    ``workdir`` is where uploaded references and collected outputs land; it must be a real
    directory because ComfyUI is handed file paths, not bytes.
    """

    name = "flux2-dev"

    def __init__(
        self,
        *,
        workdir: Path,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_s: float = 1800.0,
        turbo: bool = True,
        steps: int | None = None,
        guidance: float | None = None,
        reference_roles: Sequence[str] = (),
        send_control_as_reference: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.workdir = Path(workdir)
        self.timeout_s = timeout_s
        self.turbo = turbo
        self.steps = steps
        self.guidance = guidance
        self.reference_roles = tuple(reference_roles)
        self.send_control_as_reference = send_control_as_reference
        """Off, for the reason ``ImageSequenceSettings.control_as_reference`` records: the raster
        ``run_sequence`` compiles is a saturated red rectangle on black, and a reference is subject
        material to this pipeline."""

    # --- ReferenceEditBackend ---------------------------------------------------------------

    def text_to_image(self, prompt: str, lock: GenerationLock) -> bytes:
        """No references: how a character sheet is made before anything can be composed from it."""
        return self._run(prompt, refs=(), lock=lock, seed=lock.seed)

    def generate(
        self, prompt: str, conditioning: ControlConditioning, lock: GenerationLock, *, seed: int
    ) -> bytes:
        return self._run(prompt, refs=conditioning.reference_pngs, lock=lock, seed=seed)

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
        """The anchor leads the reference chain, then whatever else the frame is conditioned on.

        Anchor first is what makes this hub-and-spoke rather than a chain of edits: every frame is
        composed against the same first image, so drift cannot accumulate frame over frame. The
        seed moves with the attempt so a drift-failed frame genuinely regenerates instead of
        returning the same picture.
        """
        refs = (anchor_png, *conditioning.reference_pngs)
        if self.send_control_as_reference and conditioning.control_png:
            refs = (*refs, conditioning.control_png)
        return self._run(instruction, refs=refs, lock=lock, seed=lock.seed + attempt - 1)

    def ready(self) -> bool:
        async def _probe() -> bool:
            client = ComfyUIClient(self.endpoint, client_id="flux2-probe")
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

    def _run(self, prompt: str, *, refs: Sequence[bytes], lock: GenerationLock, seed: int) -> bytes:
        if len(refs) > MAX_REFERENCES:
            msg = (
                f"{self.name}: {len(refs)} references but the package tops out at "
                f"{MAX_REFERENCES}. Drop the least load-bearing rather than reordering silently — "
                "slot order is the only thing that says which reference is which."
            )
            raise SequenceError(msg)
        package = flux2_reference_package(
            len(refs),
            width=lock.width,
            height=lock.height,
            turbo=self.turbo,
            steps=self.steps,
            guidance=self.guidance,
        )
        params: dict[str, int | float | str] = {
            "prompt": prompt,
            "width": lock.width,
            "height": lock.height,
            # The Flux2Scheduler shifts its sigmas by resolution, so it is solved for the size
            # actually being rendered rather than the package's construction-time default.
            "sigma_width": lock.width,
            "sigma_height": lock.height,
            "seed": seed,
        }

        async def _go() -> bytes:
            self.workdir.mkdir(parents=True, exist_ok=True)
            client = ComfyUIClient(self.endpoint, client_id=f"flux2-{seed}")
            try:
                for i, png in enumerate(refs, start=1):
                    # Named by content digest: two frames conditioned on the same sheet reuse one
                    # upload, and a changed sheet can never be served from a stale filename.
                    digest = hashlib.sha256(png).hexdigest()[:16]
                    path = self.workdir / f"flux2_ref_{digest}.png"
                    if not path.exists():
                        path.write_bytes(png)
                    params[reference_param_name(i)] = await client.upload_image(path)
                return await self._collect(client, package, params)
            finally:
                await client.aclose()

        try:
            return asyncio.run(_go())
        except SequenceError:
            raise
        except Exception as exc:
            msg = (
                f"{self.name}: ComfyUI at {self.endpoint} failed this frame ({exc}). Start it with "
                "`just up` or check that the FLUX.2 weights are visible in its models/ tree."
            )
            raise SequenceError(msg) from exc

    async def _collect(
        self,
        client: ComfyUIClient,
        package: ComfyWorkflowPackage,
        params: dict[str, int | float | str],
    ) -> bytes:
        result = await client.run_package(
            package,
            params,
            output_dir=self.workdir / "comfy",
            timeout_s=self.timeout_s,
            # Never the websocket. ComfyUI runs here with --cache-none for the GGUF stack, which
            # makes it re-execute every node and go quiet long enough for a socket read to look
            # like a hang; the LTX path settled on polling /history for the same reason.
            collect="history",
        )
        if result.state != ExecutionState.completed or not result.outputs:
            msg = f"{self.name}: comfyui run {result.state}: {result.error}"
            raise SequenceError(msg)
        return result.outputs[0].local_path.read_bytes()
