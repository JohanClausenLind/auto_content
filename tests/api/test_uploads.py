"""POST /v1/uploads: a dropped file is identified from its bytes, stored, and described.

What matters here is that the answer is derived rather than repeated back: the kind comes from
the sniff and not from the filename or the client's content type, the stored key is
content-addressed, and the node type and suggestions that come back are what the canvas acts on.
A file type the allowlist does not carry is refused with a reason, and the reason says the sniff
decided it.
"""

from __future__ import annotations

import struct
import subprocess
import wave
from pathlib import Path

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


def a_wav(path: Path, *, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return path


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await db.commit()


@pytest.fixture
async def upload_client(sessionmaker, tmp_path: Path, monkeypatch):
    settings = load_settings(
        object_store={"backend": "filesystem", "bucket": str(tmp_path / "art")}
    )
    # open_store() reads the process settings, not the app's: point both at the same tmp bucket.
    monkeypatch.setattr("content_factory.config.get_settings", lambda: settings)
    app = create_app(settings)
    app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


async def test_a_dropped_recording_comes_back_typed_measured_and_placeable(
    upload_client, sessionmaker, tmp_path: Path
):
    wav = a_wav(tmp_path / "Take One.wav", seconds=2.0, rate=16000)
    files = {"file": ("Take One.wav", wav.read_bytes(), "application/octet-stream")}
    assert (await upload_client.post("/v1/uploads", files=files)).status_code == 401

    await seed(sessionmaker)
    r = await upload_client.post("/v1/session", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text

    r = await upload_client.post("/v1/uploads", files=files)
    assert r.status_code == 201, r.text
    body = r.json()

    # Sniffed, not taken from the client's content type (which said octet-stream).
    assert body["kind"] == "audio"
    assert body["mime"] in ("audio/x-wav", "audio/wav")
    assert body["asset_id"].endswith(".wav") and body["sha256"] in body["asset_id"]
    assert body["filename"] == "Take One.wav"
    # Measured: this is what makes the suggestion specific rather than generic.
    assert body["facts"]["sample_rate_hz"] == 16000
    assert body["facts"]["channels"] == 1
    assert 1900 <= body["facts"]["duration_ms"] <= 2100
    assert "16 kHz" in body["description"] and "mono" in body["description"]
    # Placeable: the canvas is told which node holds it and which slot carries it onward.
    assert body["node_type"] == "input.audio"
    assert body["node_slot"] == "audio"
    read = next(s for s in body["suggestions"] if s["node_type"] == "transcribe_audio")
    assert read["to_slot"] == "audio"
    assert "16 kHz" in read["why"]  # measured, so the offer is specific rather than generic


async def test_a_file_type_the_allowlist_does_not_carry_is_refused_by_its_bytes(
    upload_client, sessionmaker
):
    await seed(sessionmaker)
    await upload_client.post("/v1/session", json={"username": "owner", "password": PW})

    # An SVG renamed to .png: the sniff is what decides, and SVG is never accepted.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = await upload_client.post("/v1/uploads", files={"file": ("logo.png", svg, "image/png")})
    assert r.status_code == 422
    assert "sniffed, not extension-based" in r.json()["detail"]

    r = await upload_client.post("/v1/uploads", files={"file": ("empty.wav", b"", "audio/wav")})
    assert r.status_code == 422 and "empty" in r.json()["detail"]


async def test_a_csv_is_staged_for_ingest_rather_than_given_a_node_of_its_own(
    upload_client, sessionmaker
):
    await seed(sessionmaker)
    await upload_client.post("/v1/session", json={"username": "owner", "password": PW})

    csv = b"region,units\nnorth,12\nsouth,9\n"
    r = await upload_client.post("/v1/uploads", files={"file": ("sales.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "data"
    assert body["node_type"] == "ingest" and body["node_slot"] == "sources"
    datasets = next(s for s in body["suggestions"] if s["node_type"] == "compile_datasets")
    # No wire: the stage reads the uploads folder itself, and offering a link the graph would
    # refuse is worse than offering none.
    assert datasets["to_slot"] == ""


def a_matroska(path: Path, *, seconds: int = 2) -> Path:
    """H.264/AAC in Matroska — what a screen recorder writes, and what the allowlist refused."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate=15:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return path


async def test_a_screen_recording_arrives_as_mkv_and_is_stored_as_mp4(
    upload_client, sessionmaker, tmp_path: Path
):
    """The case the operator hit: dropping `2026-09-09 13-35-29.mkv` used to answer "file type
    'video/x-matroska' is not accepted" and send them to a terminal."""
    await seed(sessionmaker)
    await upload_client.post("/v1/session", json={"username": "owner", "password": PW})

    mkv = a_matroska(tmp_path / "2026-09-09 13-35-29.mkv")
    r = await upload_client.post(
        "/v1/uploads",
        files={"file": ("2026-09-09 13-35-29.mkv", mkv.read_bytes(), "video/x-matroska")},
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "video"
    assert body["mime"] == "video/mp4"
    assert body["filename"].endswith(".mp4")
    assert body["asset_id"].endswith(".mp4")
    # The streams were copied, not re-encoded: that is the difference between a second and a
    # minute, and between the original picture and a generation-two one.
    assert body["conversion"]["action"] == "remux"
    assert body["conversion"]["from_mime"] == "video/x-matroska"
    assert body["conversion"]["to_mime"] == "video/mp4"
    assert "no re-encode" in body["conversion"]["detail"]
    # Measured after conversion, so the facts describe the stored file.
    # Approximate on purpose: MP4's timebase reports 15 fps as 15.008, which is what a remux
    # into this container does and not something to "fix".
    assert body["facts"]["width"] == 320 and abs(body["facts"]["fps"] - 15.0) < 0.05
    assert body["node_type"] == "input.video"
    assert {s["node_type"] for s in body["suggestions"]} >= {"interpolate", "upscale_video"}


async def test_a_file_the_pipeline_can_already_read_is_not_re_encoded(
    upload_client, sessionmaker, tmp_path: Path
):
    await seed(sessionmaker)
    await upload_client.post("/v1/session", json={"username": "owner", "password": PW})

    wav = a_wav(tmp_path / "take.wav")
    r = await upload_client.post(
        "/v1/uploads", files={"file": ("take.wav", wav.read_bytes(), "audio/wav")}
    )
    assert r.status_code == 201, r.text
    # No conversion at all: the null is the assertion.
    assert r.json()["conversion"] is None
