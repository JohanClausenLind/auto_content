"""Sequences review API: listing from the output root, safe file serving, traversal refused."""

from __future__ import annotations

import json

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"

PNG = b"\x89PNG\r\n\x1a\n fake-png"


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await db.commit()


@pytest.fixture
async def seq_client(sessionmaker, tmp_path):
    seq = tmp_path / "holding-hands"
    (seq / "frames").mkdir(parents=True)
    (seq / "anchor.png").write_bytes(PNG)
    (seq / "frames" / "0000.png").write_bytes(PNG)
    (seq / "frames" / "0000.done.json").write_text(
        json.dumps(
            {
                "frame_index": 0,
                "attempts": 1,
                "cache_hit": False,
                "drift": {"locked": 0.91, "style": 0.05},
            }
        )
    )
    (seq / "holding-hands.mp4").write_bytes(b"\x00" * 32)
    (tmp_path / "secret.txt").write_text("outside the sequence dirs")
    app = create_app(load_settings(image_sequences={"output_root": str(tmp_path)}))
    app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


async def login(c):
    r = await c.post("/v1/session", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text


async def test_list_and_serve_sequence_files(seq_client, sessionmaker) -> None:
    await seed(sessionmaker)
    await login(seq_client)
    listing = (await seq_client.get("/v1/sequences")).json()
    assert [s["name"] for s in listing] == ["holding-hands"]
    seq = listing[0]
    assert seq["anchor"] and seq["videos"] == ["holding-hands.mp4"]
    assert seq["frames"][0]["drift"]["locked"] == 0.91

    r = await seq_client.get("/v1/sequences/holding-hands/files/anchor.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = await seq_client.get("/v1/sequences/holding-hands/files/frames/0000.png")
    assert r.status_code == 200 and r.content == PNG
    r = await seq_client.get("/v1/sequences/holding-hands/files/holding-hands.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"


async def test_traversal_and_disallowed_types_are_refused(seq_client, sessionmaker) -> None:
    await seed(sessionmaker)
    await login(seq_client)
    for path in (
        "/v1/sequences/holding-hands/files/../secret.txt",
        "/v1/sequences/holding-hands/files/..%2Fsecret.txt",
        "/v1/sequences/../holding-hands/files/anchor.png",
        "/v1/sequences/holding-hands/files/frames/0000.done.txt",
    ):
        r = await seq_client.get(path)
        assert r.status_code == 404, path
    # Unauthenticated access gets nothing.
    fresh = httpx.AsyncClient(
        transport=seq_client._transport,
        base_url="http://localhost:3000",
    )
    assert (await fresh.get("/v1/sequences/holding-hands/files/anchor.png")).status_code == 401
    await fresh.aclose()
