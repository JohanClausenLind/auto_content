"""`image.generate` media skill with interchangeable backends (phase-5 gate): the same typed
invocation runs through a ComfyUI workflow package or a mock cloud API unchanged. Generated
images are editorial assets, never evidence; every result carries full provenance."""

from __future__ import annotations

import asyncio
import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from pydantic import Field

from content_factory.comfyui.client import ComfyUIClient, ExecutionState
from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, sha256_hex
from content_factory.schemas.comfyui import ComfyWorkflowPackage


class GeneratedMediaRequest(SchemaModel):
    request_id: OpaqueId
    purpose: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = ""
    width: int = Field(ge=64, le=4096)
    height: int = Field(ge=64, le=4096)
    seed: int = Field(ge=0)


class GeneratedMediaResult(SchemaModel):
    request_id: OpaqueId
    image_sha256: Sha256Hex
    width: int
    height: int
    backend: str
    model_revision: str
    seed: int
    provenance: dict[str, Any]


class MediaGenerationError(Exception):
    pass


@dataclass(frozen=True)
class MediaOutput:
    result: GeneratedMediaResult
    png: bytes


class MediaBackend(ABC):
    name: str

    @abstractmethod
    def generate(self, request: GeneratedMediaRequest, *, workdir: Path) -> MediaOutput: ...


def run_media_skill(
    request: GeneratedMediaRequest, backend: MediaBackend, *, workdir: Path
) -> MediaOutput:
    """The single invocation path (identical whatever the backend): execute → verify → provenance."""  # noqa: E501
    out = backend.generate(request, workdir=workdir)
    with Image.open(_bytes_io(out.png)) as img:
        if img.format != "PNG":
            raise MediaGenerationError(f"{backend.name} returned {img.format}, not PNG")
        if (img.width, img.height) != (request.width, request.height):
            raise MediaGenerationError(
                f"{backend.name} returned {img.width}x{img.height}, requested {request.width}x{request.height}"  # noqa: E501
            )
    if out.result.image_sha256 != sha256_hex(out.png):
        raise MediaGenerationError("provenance hash does not match the returned bytes")
    return out


def _bytes_io(data: bytes):
    import io

    return io.BytesIO(data)


class ComfyUIMediaBackend(MediaBackend):
    name = "comfyui"

    def __init__(
        self,
        endpoint: str,
        package: ComfyWorkflowPackage,
        *,
        param_map: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.package = package
        # request field -> package parameter name (packages differ; the mapping is data)
        self.param_map = param_map or {"width": "width", "height": "height", "seed": "seed"}

    def generate(self, request: GeneratedMediaRequest, *, workdir: Path) -> MediaOutput:
        params: dict[str, int | float | str] = {}
        for field, param in self.param_map.items():
            params[param] = getattr(request, field)

        async def _run():
            client = ComfyUIClient(self.endpoint, client_id=f"media-{request.request_id}")
            try:
                return await client.run_package(self.package, params, output_dir=workdir / "comfy")
            finally:
                await client.aclose()

        result = asyncio.run(_run())
        if result.state != ExecutionState.completed or not result.outputs:
            raise MediaGenerationError(f"comfyui run {result.state}: {result.error}")
        png = result.outputs[0].local_path.read_bytes()
        assert result.provenance is not None
        return MediaOutput(
            result=GeneratedMediaResult(
                request_id=request.request_id,
                image_sha256=sha256_hex(png),
                width=request.width,
                height=request.height,
                backend=self.name,
                model_revision=f"{self.package.package_id}@{self.package.version}",
                seed=request.seed,
                provenance={
                    "prompt_id": result.provenance.prompt_id,
                    "workflow_sha256": result.provenance.injected_workflow_sha256,
                    "purpose": request.purpose,
                    "prompt": request.prompt,
                },
            ),
            png=png,
        )


class MockCloudMediaBackend(MediaBackend):
    """A cloud image API stand-in (httpx transport injectable): POST /v1/images → base64 PNG."""

    name = "mock-cloud"

    def __init__(
        self,
        base_url: str = "https://mock-images.invalid",
        *,
        api_key: str = "test",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=60,
        )

    def generate(self, request: GeneratedMediaRequest, *, workdir: Path) -> MediaOutput:
        resp = self._http.post(
            "/v1/images",
            json={
                "prompt": request.prompt,
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
            },
        )
        if resp.status_code >= 400:
            raise MediaGenerationError(f"cloud API {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        png = base64.b64decode(data["image_base64"])
        return MediaOutput(
            result=GeneratedMediaResult(
                request_id=request.request_id,
                image_sha256=sha256_hex(png),
                width=request.width,
                height=request.height,
                backend=self.name,
                model_revision=str(data.get("model", "mock-model-1")),
                seed=request.seed,
                provenance={
                    "purpose": request.purpose,
                    "prompt": request.prompt,
                    "provider_request_id": data.get("id", ""),
                },
            ),
            png=png,
        )
