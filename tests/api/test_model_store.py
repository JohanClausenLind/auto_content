"""/v1/models/*: the browser's view of the weight store, and installing from it.

The point of these endpoints is that an operator never has to leave the page to get a missing
weight, so what is asserted here is exactly that: the catalog says what is missing and which
workflows want it, one POST with a registry key starts the pinned install, and a key that is not
in the registry is refused rather than turned into a download.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.models.weight_install import RunResult, WeightInstaller
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


@pytest.fixture
async def store_client(sessionmaker, tmp_path: Path):
    store = tmp_path / "store"
    store.mkdir()
    settings = load_settings(
        local_services={
            "weight_store": str(store),
            "comfy_models_dir": str(tmp_path / "comfy" / "models"),
        }
    )
    app = create_app(settings)
    app.state.sessionmaker = sessionmaker
    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []

    def runner(argv: list[str], cwd, log, extra_env=None) -> RunResult:
        calls.append(argv)
        envs.append(dict(extra_env or {}))
        return RunResult(0, "ok")  # writes nothing: the job must then fail honestly

    app.state.weight_installer = WeightInstaller(
        settings=settings, runner=runner, spawn=lambda work: work()
    )
    app.state.install_calls = calls
    app.state.install_envs = envs
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


async def login(client) -> None:
    r = await client.post("/v1/session", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text


async def test_catalog_reports_what_is_missing_and_who_wants_it(store_client, sessionmaker):
    assert (await store_client.get("/v1/models/catalog")).status_code == 401

    await seed(sessionmaker)
    await login(store_client)

    body = (await store_client.get("/v1/models/catalog")).json()
    by_key = {p["key"]: p for p in body["packages"]}
    # An empty store: everything with an automatic source reads absent, and each entry carries
    # what it is for, what it costs and which workflows asked for it.
    ltx = by_key["ltx-2.5"]
    assert ltx["state"] == "absent"
    assert ltx["installable"] is True and ltx["gating"] == "auto"
    assert ltx["approx_bytes"] > 10_000_000_000
    assert "image-to-video" in ltx["wanted_by"] and ltx["required"] is True
    assert ltx["purpose"] and ltx["license"]
    # RIFE has no scriptable source and says so instead of offering a button.
    assert by_key["rife"]["installable"] is False
    assert "Google Drive" in by_key["rife"]["manual"]
    assert body["store"].endswith("store") and body["index_root"].endswith("models")
    assert {e["state"] for e in body["skill_envs"]} <= {"ready", "absent"}


async def test_install_starts_the_pinned_download_and_verifies_the_result(
    store_client, sessionmaker
):
    await seed(sessionmaker)
    await login(store_client)

    r = await store_client.post("/v1/models/install", json={"key": "gimm-vfi"})
    assert r.status_code == 202, r.text
    job = r.json()
    # The runner wrote nothing, so the job refuses to call itself complete.
    assert job["state"] == "failed" and "still missing" in job["detail"]

    argv = store_client._transport.app.state.install_calls[0]  # type: ignore[attr-defined]
    assert argv[1:3] == ["download", "GSean/GIMM-VFI"]
    assert argv[argv.index("--revision") + 1] == "ab7735cdcfbd2e03c1bf2819380a25e8a4f321d1"
    assert argv[argv.index("--local-dir") + 1].endswith("/gimm-vfi")

    listed = (await store_client.get("/v1/models/jobs")).json()
    assert [j["key"] for j in listed] == ["gimm-vfi"]


async def test_unknown_and_manual_keys_are_refused(store_client, sessionmaker):
    await seed(sessionmaker)
    await login(store_client)

    r = await store_client.post("/v1/models/install", json={"key": "not-a-family"})
    assert r.status_code == 422 and "unknown" in r.json()["detail"]

    r = await store_client.post("/v1/models/install", json={"key": "rife"})
    assert r.status_code == 422 and "Google Drive" in r.json()["detail"]


async def test_relink_is_safe_on_an_empty_store(store_client, sessionmaker):
    await seed(sessionmaker)
    await login(store_client)
    report = (await store_client.post("/v1/models/relink")).json()
    assert report["comfy"] == [] and report["index"] == []
    assert "ltx-2.5" in report["skipped"]


async def test_the_gated_download_token_is_stored_sealed_and_used_by_installs(
    store_client, sessionmaker
):
    await seed(sessionmaker)
    await login(store_client)

    assert (await store_client.get("/v1/models/hf-access")).json() == {
        "present": False,
        "handle": "",
        "stored_at": None,
    }

    r = await store_client.put("/v1/models/hf-access", json={"token": "hf_pretendtoken123"})
    assert r.status_code == 204, r.text
    access = (await store_client.get("/v1/models/hf-access")).json()
    assert access["present"] is True and access["handle"] == "huggingface.co"
    # The token itself never comes back out of the API.
    assert "hf_pretendtoken123" not in (await store_client.get("/v1/models/hf-access")).text

    r = await store_client.post("/v1/models/install", json={"key": "gimm-vfi"})
    assert r.status_code == 202, r.text
    envs = store_client._transport.app.state.install_envs  # type: ignore[attr-defined]
    assert envs[0] == {"HF_TOKEN": "hf_pretendtoken123"}
    argv = store_client._transport.app.state.install_calls[0]  # type: ignore[attr-defined]
    assert "hf_pretendtoken123" not in " ".join(argv)

    assert (await store_client.delete("/v1/models/hf-access")).status_code == 204
    assert (await store_client.get("/v1/models/hf-access")).json()["present"] is False
