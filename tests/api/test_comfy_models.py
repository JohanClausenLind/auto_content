"""Model inventory + one-click download API: config-derived roots, allowlisted downloads."""

from __future__ import annotations

import json

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.comfyui.downloads import DownloadManager, RunnerResult
from content_factory.config import load_settings
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await db.commit()


def fake_runner_factory(calls):
    def runner(args):
        calls.append(args)
        if "download-status" in args:
            data = {"id": "dl_1", "status": "complete"}
        else:
            data = {"download_id": "dl_1", "status": "running"}
        return RunnerResult(0, json.dumps({"ok": True, "data": data, "error": None}), "")

    return runner


@pytest.fixture
async def comfy_client(sessionmaker, tmp_path):
    models = tmp_path / "ws" / "models"
    (models / "loras").mkdir(parents=True)
    (models / "loras" / "style.safetensors").write_bytes(b"x" * 8)
    extra = tmp_path / "weights"
    extra.mkdir()
    (extra / "shard-00001.safetensors").write_bytes(b"y" * 16)
    app = create_app(
        load_settings(
            comfyui={
                "workspace": str(tmp_path / "ws"),
                "managed_by_comfy_cli": False,
                "extra_model_roots": (str(extra),),
            }
        )
    )
    app.state.sessionmaker = sessionmaker
    calls: list[list[str]] = []
    app.state.comfy_downloads = DownloadManager(runner=fake_runner_factory(calls))
    app.state.comfy_calls = calls
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


async def test_inventory_requires_auth_and_lists_configured_roots(comfy_client, sessionmaker):
    assert (await comfy_client.get("/v1/comfy/models")).status_code == 401

    await seed(sessionmaker)
    r = await comfy_client.post("/v1/session", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text

    body = (await comfy_client.get("/v1/comfy/models")).json()
    assert [(root["source"], root["exists"]) for root in body["roots"]] == [
        ("settings", True),
        ("extra", True),
    ]
    by_name = {m["filename"]: m for m in body["models"]}
    assert by_name["style.safetensors"]["kind"] == "loras"
    assert by_name["style.safetensors"]["relative_path"] == "loras/style.safetensors"
    assert by_name["shard-00001.safetensors"]["kind"] == "weights"
    assert by_name["shard-00001.safetensors"]["size_bytes"] == 16


async def test_one_click_download_starts_polls_and_refuses_bad_hosts(comfy_client, sessionmaker):
    await seed(sessionmaker)
    r = await comfy_client.post("/v1/session", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text

    body = {
        "url": "https://huggingface.co/acme/pack/resolve/main/extra.safetensors",
        "relative_path": "models/loras",
        "filename": "extra.safetensors",
    }
    r = await comfy_client.post("/v1/comfy/models/download", json=body)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["state"] == "running" and job["filename"] == "extra.safetensors"

    listed = (await comfy_client.get("/v1/comfy/models/downloads")).json()
    assert listed[0]["state"] == "complete"  # fake status poll reports done

    r = await comfy_client.post(
        "/v1/comfy/models/download",
        json={**body, "url": "https://evil.example/x.safetensors"},
    )
    assert r.status_code == 422
    assert "not an allowlisted" in r.json()["detail"]
