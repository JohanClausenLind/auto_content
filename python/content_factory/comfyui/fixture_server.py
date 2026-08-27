"""A deterministic ComfyUI look-alike for tests (HTTP + WS), covering the routes our adapter uses.

It implements a tiny node set (EmptyImage, SaveImage, SlowNode) with real image output so tests can
assert provenance hashes. It is NOT ComfyUI and never pretends to be in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import uuid
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from PIL import Image

OBJECT_INFO: dict[str, Any] = {
    "EmptyImage": {
        "input": {
            "required": {
                "width": ["INT", {"default": 512, "min": 1, "max": 16384, "step": 8}],
                "height": ["INT", {"default": 512, "min": 1, "max": 16384, "step": 8}],
                "batch_size": ["INT", {"default": 1, "min": 1, "max": 4096}],
                "color": [
                    "INT",
                    {"default": 0, "min": 0, "max": 16777215, "step": 1, "display": "color"},
                ],
            }
        },
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "name": "EmptyImage",
        "category": "image",
        "output_node": False,
    },
    "SaveImage": {
        "input": {
            "required": {
                "images": ["IMAGE"],
                "filename_prefix": ["STRING", {"default": "ComfyUI"}],
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        },
        "output": [],
        "output_name": [],
        "name": "SaveImage",
        "category": "image",
        "output_node": True,
    },
    "SlowNode": {
        "input": {
            "required": {
                "images": ["IMAGE"],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
            }
        },
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "name": "SlowNode",
        "category": "fixture",
        "output_node": False,
    },
}


def create_app(*, step_delay_s: float = 0.02) -> FastAPI:
    app = FastAPI(title="comfyui-fixture")
    state: dict[str, Any] = {
        "history": {},
        "files": {},
        "sockets": {},
        "queue": [],
        "running": None,
        "interrupt": asyncio.Event(),
        "lock": asyncio.Lock(),
        "tasks": set(),
    }

    def _spawn(coro: Any) -> None:
        task = asyncio.create_task(coro)
        state["tasks"].add(task)
        task.add_done_callback(state["tasks"].discard)

    async def broadcast(client_id: str | None, msg: dict[str, Any]) -> None:
        targets = (
            [state["sockets"].get(client_id)] if client_id else list(state["sockets"].values())
        )
        for ws in targets:
            if ws is None:
                continue
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps(msg))

    @app.get("/system_stats")
    async def system_stats() -> dict[str, Any]:
        return {
            "system": {
                "os": "fixture",
                "comfyui_version": "0.0.0-fixture",
                "python_version": "3.12",
                "embedded_python": False,
            },
            "devices": [{"name": "fixture-cpu", "type": "cpu", "vram_total": 0, "vram_free": 0}],
        }

    @app.get("/object_info")
    async def object_info() -> dict[str, Any]:
        return OBJECT_INFO

    @app.get("/object_info/{node}")
    async def object_info_one(node: str) -> dict[str, Any]:
        return {node: OBJECT_INFO[node]} if node in OBJECT_INFO else {}

    @app.get("/queue")
    async def queue() -> dict[str, Any]:
        return {
            "queue_running": [state["running"]] if state["running"] else [],
            "queue_pending": list(state["queue"]),
        }

    @app.post("/queue")
    async def queue_delete(req: Request) -> dict[str, Any]:
        body = await req.json()
        for pid in body.get("delete", []):
            state["queue"] = [q for q in state["queue"] if q["prompt_id"] != pid]
        return {}

    @app.post("/interrupt")
    async def interrupt() -> Response:
        state["interrupt"].set()
        return Response(status_code=200)

    @app.post("/free")
    async def free() -> Response:
        return Response(status_code=200)

    @app.get("/history/{prompt_id}")
    async def history(prompt_id: str) -> dict[str, Any]:
        return {prompt_id: state["history"][prompt_id]} if prompt_id in state["history"] else {}

    @app.get("/view")
    async def view(filename: str, subfolder: str = "", type: str = "output") -> Response:
        data = state["files"].get((type, subfolder, filename))
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="image/png")

    @app.post("/prompt")
    async def prompt(req: Request) -> Response:
        body = await req.json()
        workflow = body.get("prompt", {})
        client_id = body.get("client_id")
        errors: dict[str, Any] = {}
        for node_id, node in workflow.items():
            ct = node.get("class_type")
            if ct not in OBJECT_INFO:
                errors[node_id] = {"class_type": ct, "errors": [{"type": "invalid_node_type"}]}
        if errors:
            return JSONResponse(
                {"error": {"type": "prompt_outputs_failed_validation"}, "node_errors": errors},
                status_code=400,
            )
        prompt_id = uuid.uuid4().hex
        state["queue"].append(
            {"prompt_id": prompt_id, "client_id": client_id, "workflow": workflow}
        )
        _spawn(execute())
        return JSONResponse(
            {"prompt_id": prompt_id, "number": len(state["queue"]), "node_errors": {}}
        )

    async def execute() -> None:
        async with state["lock"]:
            if not state["queue"]:
                return
            job = state["queue"].pop(0)
            state["running"] = job
            state["interrupt"].clear()
            pid, cid, wf = job["prompt_id"], job["client_id"], job["workflow"]
            await broadcast(cid, {"type": "execution_start", "data": {"prompt_id": pid}})
            images: dict[str, Image.Image] = {}
            outputs: dict[str, Any] = {}
            status = "success"
            for node_id in sorted(wf, key=lambda k: int(k)):
                node = wf[node_id]
                await broadcast(
                    cid, {"type": "executing", "data": {"node": node_id, "prompt_id": pid}}
                )
                ct, inputs = node["class_type"], node["inputs"]
                if ct == "EmptyImage":
                    c = int(inputs["color"])
                    images[node_id] = Image.new(
                        "RGB",
                        (int(inputs["width"]), int(inputs["height"])),
                        ((c >> 16) & 255, (c >> 8) & 255, c & 255),
                    )
                elif ct == "SlowNode":
                    steps = int(inputs["steps"])
                    for i in range(steps):
                        if state["interrupt"].is_set():
                            status = "interrupted"
                            break
                        await asyncio.sleep(step_delay_s)
                        await broadcast(
                            cid,
                            {
                                "type": "progress",
                                "data": {
                                    "value": i + 1,
                                    "max": steps,
                                    "prompt_id": pid,
                                    "node": node_id,
                                },
                            },
                        )
                    if status == "interrupted":
                        break
                    images[node_id] = images[str(inputs["images"][0])]
                elif ct == "SaveImage":
                    src = images[str(inputs["images"][0])]
                    buf = io.BytesIO()
                    src.save(buf, format="PNG", compress_level=6)
                    fname = f"{inputs['filename_prefix']}_{len(state['files']):05d}_.png"
                    state["files"][("output", "", fname)] = buf.getvalue()
                    outputs[node_id] = {
                        "images": [{"filename": fname, "subfolder": "", "type": "output"}]
                    }
                    await broadcast(
                        cid,
                        {
                            "type": "executed",
                            "data": {"node": node_id, "prompt_id": pid, "output": outputs[node_id]},
                        },
                    )
            if status == "interrupted":
                state["history"][pid] = {
                    "prompt": [0, pid, wf, {}, []],
                    "outputs": {},
                    "status": {
                        "status_str": "error",
                        "completed": False,
                        "messages": [["execution_interrupted", {"prompt_id": pid}]],
                    },
                }
                await broadcast(cid, {"type": "execution_interrupted", "data": {"prompt_id": pid}})
            else:
                state["history"][pid] = {
                    "prompt": [0, pid, wf, {}, list(outputs)],
                    "outputs": outputs,
                    "status": {"status_str": "success", "completed": True, "messages": []},
                }
                await broadcast(cid, {"type": "execution_success", "data": {"prompt_id": pid}})
                await broadcast(
                    cid, {"type": "executing", "data": {"node": None, "prompt_id": pid}}
                )
            state["running"] = None
        if state["queue"]:
            _spawn(execute())

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        client_id = websocket.query_params.get("clientId") or uuid.uuid4().hex
        state["sockets"][client_id] = websocket
        await websocket.send_text(
            json.dumps(
                {
                    "type": "status",
                    "data": {
                        "status": {"exec_info": {"queue_remaining": len(state["queue"])}},
                        "sid": client_id,
                    },
                }
            )
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            state["sockets"].pop(client_id, None)

    return app
