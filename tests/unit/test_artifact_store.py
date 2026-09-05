from __future__ import annotations

import os
from pathlib import Path

import pytest

from content_factory.artifacts import (
    ArtifactStore,
    FilesystemArtifactStore,
    S3ArtifactStore,
    SignedUrlSigner,
)
from content_factory.artifacts.store import ArtifactStoreError, WorkspaceMismatchError

WS_A = "ws_aaaaaaaaaaaa"
WS_B = "ws_bbbbbbbbbbbb"


@pytest.fixture(params=["filesystem", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ArtifactStore:
    if request.param == "filesystem":
        return FilesystemArtifactStore(tmp_path / "artifacts")
    from moto import mock_aws

    ctx = mock_aws()
    ctx.start()
    request.addfinalizer(ctx.stop)
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    s3 = S3ArtifactStore(
        "cf-test", endpoint_url=None, region="us-east-1", access_key="test", secret_key="test"
    )
    s3.ensure_bucket()
    return s3


def test_put_is_content_addressed_immutable_and_workspace_scoped(store: ArtifactStore) -> None:
    ref = store.put_bytes(WS_A, "renders", b"hello", content_type="text/plain", ext="txt")
    again = store.put_bytes(WS_A, "renders", b"hello", content_type="text/plain", ext="txt")
    assert ref == again
    assert ref.key.startswith(f"{WS_A}/renders/")
    assert ref.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert store.get_bytes(WS_A, ref.key) == b"hello"
    assert store.exists(WS_A, ref.key)
    assert store.list_keys(WS_A) == [ref.key]
    assert store.list_keys(WS_B) == []
    # Another workspace can never read through the interface, even with the exact key.
    with pytest.raises(WorkspaceMismatchError):
        store.get_bytes(WS_B, ref.key)
    with pytest.raises(WorkspaceMismatchError):
        store.exists(WS_B, ref.key)


def test_invalid_keys_are_rejected(store: ArtifactStore) -> None:
    for bad in ["../etc/passwd", f"{WS_A}/renders/../x", f"{WS_A}/renders/zz/nothex", "ws_x/a/b"]:
        with pytest.raises(ArtifactStoreError):
            store.exists(WS_A, bad)


def test_put_file_and_delete_workspace(store: ArtifactStore, tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 100)
    ref = store.put_file(WS_A, "renders", f)
    assert ref.key.endswith(".mp4")
    assert ref.content_type == "video/mp4"
    store.put_bytes(WS_B, "renders", b"other", ext="bin")
    assert store.delete_workspace(WS_A) == 1
    assert store.list_keys(WS_A) == []
    assert len(store.list_keys(WS_B)) == 1


def test_signed_urls_expire_and_reject_tampering() -> None:
    signer = SignedUrlSigner(b"0123456789abcdef0123456789abcdef")
    key = f"{WS_A}/renders/2c/{'2c' * 32}.txt"
    url = signer.sign(key, ttl_seconds=60, now=1_000_000)
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert signer.verify(key, exp, sig, now=1_000_030)
    assert not signer.verify(key, exp, sig, now=1_000_061)  # expired
    assert not signer.verify(key.replace("txt", "mp4"), exp, sig, now=1_000_030)  # different key
    assert not signer.verify(key, exp + 1, sig, now=1_000_030)  # tampered expiry


def test_open_store_honors_the_backend_setting(tmp_path: Path, monkeypatch) -> None:
    """`object_store.backend` must actually select the backend.

    Regression: every production caller used to construct FilesystemArtifactStore directly, so
    setting backend="s3" was inert and artifacts silently kept landing on local disk.
    """
    from content_factory.artifacts import open_store
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    fs = open_store(tmp_path / "artifacts")
    assert isinstance(fs, FilesystemArtifactStore)
    assert fs.root == tmp_path / "artifacts"  # the per-run root override still wins

    from moto import mock_aws

    ctx = mock_aws()
    ctx.start()
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY", "test")
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "test")
    monkeypatch.setenv("CF__OBJECT_STORE__BACKEND", "s3")
    monkeypatch.setenv("CF__OBJECT_STORE__BUCKET", "cf-backend-switch")
    monkeypatch.setenv("CF__OBJECT_STORE__REGION", "us-east-1")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        s3 = open_store(tmp_path / "artifacts")  # root is meaningless for object storage
        assert isinstance(s3, S3ArtifactStore)
        assert s3.bucket == "cf-backend-switch"
    finally:
        ctx.stop()
        get_settings.cache_clear()  # type: ignore[attr-defined]
