"""ComfyUIProvider (19.1): typed adapter over the documented ComfyUI HTTP/WS API.

Production jobs submit *validated, allowlisted* API-format workflows from a signed
:class:`ComfyWorkflowPackage`. Parameter injection is limited to the package's declared bindings —
never arbitrary node or path mutation. Everything is recorded as :class:`ComfyProvenance`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
import websockets

from content_factory.schemas.base import canonical_dumps, sha256_hex
from content_factory.schemas.comfyui import ComfyProvenance, ComfyWorkflowPackage, ParameterBinding


class ComfyError(Exception):
    """Permanent error (bad workflow, validation failure, rejected parameters)."""


class ComfyTransientError(ComfyError):
    """Retryable error (connection refused, timeout, 5xx)."""


class ComfyCancelledError(ComfyError):
    """The prompt was interrupted before completion."""


class WorkflowValidationError(ComfyError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class ExecutionState(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


@dataclass(frozen=True)
class ProgressEvent:
    # status | execution_start | executing | progress | executed | execution_error |
    # execution_cached | execution_interrupted | execution_success
    kind: str
    prompt_id: str | None
    node: str | None = None
    value: int | None = None
    max: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputFile:
    filename: str
    subfolder: str
    type: str
    node_id: str


@dataclass(frozen=True)
class ImportedOutput:
    node_id: str
    filename: str
    local_path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExecutionResult:
    prompt_id: str
    state: ExecutionState
    outputs: tuple[ImportedOutput, ...]
    provenance: ComfyProvenance | None
    error: str | None = None


def inject_parameters(
    package: ComfyWorkflowPackage, params: Mapping[str, int | float | str]
) -> dict[str, dict[str, Any]]:
    """Return a deep copy of the package workflow with ONLY declared bindings overwritten."""
    bindings: dict[str, ParameterBinding] = {b.name: b for b in package.parameters}
    unknown = sorted(set(params) - set(bindings))
    if unknown:
        raise WorkflowValidationError([f"undeclared parameter(s): {unknown}"])
    workflow: dict[str, dict[str, Any]] = json.loads(json.dumps(package.api_workflow))
    for name, value in params.items():
        b = bindings[name]
        if b.kind in {"int", "seed"} and not (
            isinstance(value, int) and not isinstance(value, bool)
        ):
            raise WorkflowValidationError([f"parameter {name!r} must be an integer"])
        if b.kind == "float" and not isinstance(value, int | float):
            raise WorkflowValidationError([f"parameter {name!r} must be a number"])
        if b.kind in {"string", "image_ref"} and not isinstance(value, str):
            raise WorkflowValidationError([f"parameter {name!r} must be a string"])
        if b.kind == "image_ref" and (
            "/" in str(value) or "\\" in str(value) or ".." in str(value)
        ):
            raise WorkflowValidationError([f"parameter {name!r} must be a bare uploaded filename"])
        if isinstance(value, int | float) and not isinstance(value, bool):
            if b.minimum is not None and value < b.minimum:
                raise WorkflowValidationError([f"parameter {name!r} below minimum {b.minimum}"])
            if b.maximum is not None and value > b.maximum:
                raise WorkflowValidationError([f"parameter {name!r} above maximum {b.maximum}"])
        workflow[b.node_id]["inputs"][b.input_name] = value
    return workflow


def validate_against_object_info(
    workflow: Mapping[str, Mapping[str, Any]],
    object_info: Mapping[str, Any],
    *,
    allowed_node_types: set[str] | None = None,
) -> list[str]:
    """Deterministic pre-submit validation against the server's node schemas."""
    problems: list[str] = []
    for node_id, node in workflow.items():
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            problems.append(f"node {node_id}: missing class_type")
            continue
        if allowed_node_types is not None and class_type not in allowed_node_types:
            problems.append(f"node {node_id}: node type {class_type!r} is not allowlisted")
        info = object_info.get(class_type)
        if info is None:
            problems.append(
                f"node {node_id}: node type {class_type!r} is not installed on the server"
            )
            continue
        declared = info.get("input", {})
        required = declared.get("required", {})
        optional = declared.get("optional", {})
        inputs = node.get("inputs", {})
        for name in required:
            if name not in inputs:
                problems.append(f"node {node_id} ({class_type}): missing required input {name!r}")
        for name, value in inputs.items():
            spec = required.get(name) or optional.get(name)
            if spec is None:
                problems.append(f"node {node_id} ({class_type}): unknown input {name!r}")
                continue
            if isinstance(value, list) and len(value) == 2:
                src = str(value[0])
                if src not in workflow:
                    problems.append(f"node {node_id}: input {name!r} links to missing node {src!r}")
                continue
            kind = spec[0] if isinstance(spec, list) and spec else None
            if kind == "INT" and not (isinstance(value, int) and not isinstance(value, bool)):
                problems.append(f"node {node_id}: input {name!r} must be INT")
            elif kind == "FLOAT" and not isinstance(value, int | float):
                problems.append(f"node {node_id}: input {name!r} must be FLOAT")
            elif kind == "STRING" and not isinstance(value, str):
                problems.append(f"node {node_id}: input {name!r} must be STRING")
            elif isinstance(kind, list) and value not in kind:
                problems.append(f"node {node_id}: input {name!r} value {value!r} not in choices")
            if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
                opts = spec[1]
                if isinstance(value, int | float) and not isinstance(value, bool):
                    if "min" in opts and value < opts["min"]:
                        problems.append(f"node {node_id}: input {name!r} below min {opts['min']}")
                    if "max" in opts and value > opts["max"]:
                        problems.append(f"node {node_id}: input {name!r} above max {opts['max']}")
    return problems


class ComfyUIClient:
    """Async client for one ComfyUI endpoint. One instance per endpoint; safe to reuse."""

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or uuid.uuid4().hex
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- discovery ---------------------------------------------------------------------------
    async def system_stats(self) -> dict[str, Any]:
        return await self._get_json("/system_stats")

    async def object_info(self, node_type: str | None = None) -> dict[str, Any]:
        path = "/object_info" if node_type is None else f"/object_info/{node_type}"
        return await self._get_json(path)

    async def queue(self) -> dict[str, Any]:
        return await self._get_json("/queue")

    async def history(self, prompt_id: str) -> dict[str, Any]:
        return await self._get_json(f"/history/{prompt_id}")

    # -- execution ---------------------------------------------------------------------------
    async def submit(self, workflow: Mapping[str, Mapping[str, Any]]) -> tuple[str, int]:
        body = {"prompt": workflow, "client_id": self.client_id}
        try:
            resp = await self._http.post("/prompt", json=body)
        except httpx.TransportError as exc:
            raise ComfyTransientError(str(exc)) from exc
        if resp.status_code >= 500:
            raise ComfyTransientError(f"POST /prompt -> {resp.status_code}")
        if resp.status_code >= 400:
            detail: Any
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise WorkflowValidationError([f"server rejected prompt: {json.dumps(detail)[:2000]}"])
        data = resp.json()
        if data.get("node_errors"):
            raise WorkflowValidationError(
                [f"node_errors: {json.dumps(data['node_errors'])[:2000]}"]
            )
        return str(data["prompt_id"]), int(data.get("number", 0))

    async def interrupt(self) -> None:
        await self._post("/interrupt")

    async def free(self, *, unload_models: bool = False, free_memory: bool = True) -> None:
        await self._post("/free", json={"unload_models": unload_models, "free_memory": free_memory})

    async def delete_queued(self, prompt_id: str) -> None:
        await self._post("/queue", json={"delete": [prompt_id]})

    async def upload_image(
        self, path: Path, *, subfolder: str = "", overwrite: bool = False
    ) -> str:
        with path.open("rb") as fh:
            files = {"image": (path.name, fh, "application/octet-stream")}
            data = {
                "subfolder": subfolder,
                "overwrite": "true" if overwrite else "false",
                "type": "input",
            }
            resp = await self._http.post("/upload/image", files=files, data=data)
        resp.raise_for_status()
        return str(resp.json()["name"])

    async def download_view(self, out: OutputFile, dest: Path) -> ImportedOutput:
        params = {"filename": out.filename, "subfolder": out.subfolder, "type": out.type}
        resp = await self._http.get("/view", params=params)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)  # atomic temp-then-rename
        return ImportedOutput(
            node_id=out.node_id,
            filename=out.filename,
            local_path=dest,
            sha256=sha256_hex(resp.content),
            size_bytes=len(resp.content),
        )

    async def events(
        self, *, stop: asyncio.Event, connected: asyncio.Event | None = None
    ) -> AsyncIterator[ProgressEvent]:
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        async with websockets.connect(
            f"{ws_url}/ws?clientId={self.client_id}", max_size=None
        ) as ws:
            if connected is not None:
                connected.set()
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    continue  # binary preview frames: ignored
                msg = json.loads(raw)
                data = msg.get("data", {})
                yield ProgressEvent(
                    kind=str(msg.get("type")),
                    prompt_id=data.get("prompt_id"),
                    node=data.get("node"),
                    value=data.get("value"),
                    max=data.get("max"),
                    payload=data,
                )

    async def run_package(
        self,
        package: ComfyWorkflowPackage,
        params: Mapping[str, int | float | str],
        *,
        output_dir: Path,
        allowed_node_types: set[str] | None = None,
        timeout_s: float = 600.0,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        cancel: asyncio.Event | None = None,
    ) -> ExecutionResult:
        """Validate -> submit -> stream progress -> import outputs -> provenance. Cancellable."""
        workflow = inject_parameters(package, params)
        info = await self.object_info()
        problems = validate_against_object_info(
            workflow, info, allowed_node_types=allowed_node_types
        )
        if problems:
            raise WorkflowValidationError(problems)
        stats = await self.system_stats()
        comfy_version = str(stats.get("system", {}).get("comfyui_version", "unknown"))

        started = datetime.now(UTC)
        stop = asyncio.Event()
        connected = asyncio.Event()
        state = ExecutionState.queued
        error: str | None = None
        prompt_id: str | None = None

        async def watch() -> None:
            nonlocal state, error
            async for ev in self.events(stop=stop, connected=connected):
                if ev.prompt_id is None or ev.prompt_id != prompt_id:
                    continue  # status/heartbeat or another client's prompt
                if on_progress:
                    on_progress(ev)
                if ev.kind in {"execution_start", "executing", "progress"}:
                    state = ExecutionState.running
                    if ev.kind == "executing" and ev.node is None:
                        state = ExecutionState.completed
                        stop.set()
                elif ev.kind == "execution_success":
                    state = ExecutionState.completed
                    stop.set()
                elif ev.kind == "execution_error":
                    state = ExecutionState.failed
                    error = json.dumps(dict(ev.payload))[:2000]
                    stop.set()
                elif ev.kind == "execution_interrupted":
                    state = ExecutionState.cancelled
                    stop.set()

        watcher = asyncio.create_task(watch())
        # The socket must be open before the prompt is queued, or a fast job's events are lost.
        try:
            await asyncio.wait_for(connected.wait(), timeout=10)
        except TimeoutError as exc:
            watcher.cancel()
            raise ComfyTransientError("websocket did not connect") from exc
        prompt_id, _ = await self.submit(workflow)

        async def cancel_watch() -> None:
            if cancel is None:
                return
            await cancel.wait()
            await self.delete_queued(prompt_id)  # type: ignore[arg-type]
            await self.interrupt()

        canceller = asyncio.create_task(cancel_watch())
        try:
            await asyncio.wait_for(stop.wait(), timeout=timeout_s)
        except TimeoutError:
            await self.interrupt()
            state = ExecutionState.failed
            error = f"timed out after {timeout_s}s"
        finally:
            stop.set()
            canceller.cancel()
            watcher.cancel()
            for t in (watcher, canceller):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t

        # Reconcile with /history regardless of what the socket said (a restart can drop events).
        hist = (await self.history(prompt_id)).get(prompt_id, {})
        status = hist.get("status", {})
        if status.get("status_str") == "success":
            state = ExecutionState.completed
        elif status.get("status_str") == "error" and state != ExecutionState.cancelled:
            state = ExecutionState.failed
            error = error or json.dumps(status.get("messages", []))[:2000]

        outputs: list[ImportedOutput] = []
        if state == ExecutionState.completed:
            for node_id, out in hist.get("outputs", {}).items():
                for img in out.get("images", []):
                    of = OutputFile(
                        img["filename"],
                        img.get("subfolder", ""),
                        img.get("type", "output"),
                        node_id,
                    )
                    outputs.append(
                        await self.download_view(of, output_dir / node_id / img["filename"])
                    )
            missing = [n for n in package.expected_outputs if n not in hist.get("outputs", {})]
            if missing:
                state = ExecutionState.failed
                error = f"expected output nodes produced nothing: {missing}"

        finished = datetime.now(UTC)
        provenance = None
        if state == ExecutionState.completed:
            provenance = ComfyProvenance(
                endpoint=self.base_url,
                package_id=package.package_id,
                package_version=package.version,
                comfyui_version=comfy_version,
                prompt_id=prompt_id,
                client_id=self.client_id,
                parameters=dict(params),
                injected_workflow_sha256=hashlib.sha256(
                    canonical_dumps(workflow).encode()
                ).hexdigest(),
                output_files=tuple(str(o.local_path.relative_to(output_dir)) for o in outputs),
                output_sha256=tuple(o.sha256 for o in outputs),
                seed=next((int(v) for k, v in params.items() if k == "seed"), None),
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
            )
        if state == ExecutionState.cancelled:
            return ExecutionResult(prompt_id, state, (), None, error="cancelled")
        return ExecutionResult(prompt_id, state, tuple(outputs), provenance, error)

    # -- helpers ------------------------------------------------------------------------------
    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            resp = await self._http.get(path)
        except httpx.TransportError as exc:
            raise ComfyTransientError(str(exc)) from exc
        if resp.status_code >= 500:
            raise ComfyTransientError(f"GET {path} -> {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: Any | None = None) -> None:
        try:
            resp = await self._http.post(path, json=json)
        except httpx.TransportError as exc:
            raise ComfyTransientError(str(exc)) from exc
        if resp.status_code >= 500:
            raise ComfyTransientError(f"POST {path} -> {resp.status_code}")
        resp.raise_for_status()
