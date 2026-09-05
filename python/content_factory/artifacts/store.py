"""ArtifactStore (section 8): immutable, workspace-scoped, content-addressed object storage.

Two backends behind one interface: filesystem (default) and S3-compatible (SeaweedFS/AWS/…).
Keys are ``<workspace_id>/<kind>/<sha256[:2]>/<sha256>.<ext>``. Writes are atomic
(temp-then-rename or single PUT); an existing object with the same hash is never rewritten.
Browsers receive short-lived signed URLs; provider credentials never leave the server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import os
import re
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from content_factory.schemas.base import sha256_hex

_KEY_RE = re.compile(
    r"^ws_[A-Za-z0-9_-]{8,32}/[a-z][a-z0-9_-]{0,31}/[a-f0-9]{2}/[a-f0-9]{64}(\.[a-z0-9]{1,8})?$"
)


class ArtifactStoreError(Exception):
    pass


class WorkspaceMismatchError(ArtifactStoreError):
    pass


@dataclass(frozen=True)
class ArtifactRef:
    workspace_id: str
    key: str
    sha256: str
    size_bytes: int
    content_type: str

    @staticmethod
    def build(
        workspace_id: str, kind: str, sha256: str, size_bytes: int, content_type: str, ext: str
    ) -> ArtifactRef:
        suffix = f".{ext.lstrip('.')}" if ext else ""
        key = f"{workspace_id}/{kind}/{sha256[:2]}/{sha256}{suffix}"
        if not _KEY_RE.match(key):
            msg = f"invalid artifact key {key!r}"
            raise ArtifactStoreError(msg)
        return ArtifactRef(workspace_id, key, sha256, size_bytes, content_type)


def _validate_key(key: str, workspace_id: str) -> None:
    if not _KEY_RE.match(key):
        msg = f"invalid artifact key {key!r}"
        raise ArtifactStoreError(msg)
    if not key.startswith(workspace_id + "/"):
        raise WorkspaceMismatchError(f"key {key!r} does not belong to workspace {workspace_id!r}")


class SignedUrlSigner:
    """HMAC-signed, expiring artifact URLs served by the API (`/v1/artifacts/{key}?exp&sig`)."""

    def __init__(self, secret: bytes, base_url: str = "/v1/artifacts") -> None:
        if len(secret) < 16:
            msg = "signing secret too short"
            raise ValueError(msg)
        self._secret = secret
        self._base = base_url.rstrip("/")

    def sign(self, key: str, ttl_seconds: int, *, now: float | None = None) -> str:
        exp = int((now if now is not None else time.time()) + ttl_seconds)
        sig = self._sig(key, exp)
        return f"{self._base}/{key}?exp={exp}&sig={sig}"

    def verify(self, key: str, exp: int, sig: str, *, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        if current > exp:
            return False
        return hmac.compare_digest(self._sig(key, exp), sig)

    def _sig(self, key: str, exp: int) -> str:
        mac = hmac.new(self._secret, f"{key}\n{exp}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


class ArtifactStore(ABC):
    @abstractmethod
    def put_bytes(
        self,
        workspace_id: str,
        kind: str,
        data: bytes,
        *,
        content_type: str | None = None,
        ext: str = "",
    ) -> ArtifactRef: ...

    @abstractmethod
    def put_file(
        self, workspace_id: str, kind: str, path: Path, *, content_type: str | None = None
    ) -> ArtifactRef: ...

    @abstractmethod
    def get_bytes(self, workspace_id: str, key: str) -> bytes: ...

    @abstractmethod
    def open(self, workspace_id: str, key: str) -> BinaryIO: ...

    @abstractmethod
    def exists(self, workspace_id: str, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, workspace_id: str, kind: str | None = None) -> list[str]: ...

    @abstractmethod
    def delete_workspace(self, workspace_id: str) -> int:
        """Delete every object of a workspace (data lifecycle). Returns the count removed."""


def _guess(content_type: str | None, ext: str) -> str:
    if content_type:
        return content_type
    guessed, _ = mimetypes.guess_type(f"x.{ext.lstrip('.')}") if ext else (None, None)
    return guessed or "application/octet-stream"


class FilesystemArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            msg = "path escapes store root"
            raise ArtifactStoreError(msg)
        return p

    def put_bytes(self, workspace_id, kind, data, *, content_type=None, ext=""):  # type: ignore[override]
        digest = sha256_hex(data)
        ref = ArtifactRef.build(
            workspace_id, kind, digest, len(data), _guess(content_type, ext), ext
        )
        dest = self._path(ref.key)
        if dest.exists():
            return ref  # immutable: identical content already stored
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return ref

    def put_file(self, workspace_id, kind, path, *, content_type=None):  # type: ignore[override]
        h = hashlib.sha256()
        size = 0
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        ext = path.suffix.lstrip(".").lower()
        ref = ArtifactRef.build(
            workspace_id, kind, h.hexdigest(), size, _guess(content_type, ext), ext
        )
        dest = self._path(ref.key)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
            os.close(fd)
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        return ref

    def get_bytes(self, workspace_id, key):  # type: ignore[override]
        _validate_key(key, workspace_id)
        return self._path(key).read_bytes()

    def open(self, workspace_id, key):  # type: ignore[override]
        _validate_key(key, workspace_id)
        return self._path(key).open("rb")

    def exists(self, workspace_id, key):  # type: ignore[override]
        _validate_key(key, workspace_id)
        return self._path(key).exists()

    def list_keys(self, workspace_id, kind=None):  # type: ignore[override]
        base = self.root / workspace_id / (kind or "")
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in base.rglob("*")
            if p.is_file() and not p.name.startswith(".tmp-")
        )

    def delete_workspace(self, workspace_id):  # type: ignore[override]
        base = self.root / workspace_id
        if not base.exists():
            return 0
        n = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base)
        return n


class S3ArtifactStore(ArtifactStore):
    """S3-compatible backend (boto3). Credentials come from env names in settings, never config."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None,
        region: str | None,
        access_key: str | None,
        secret_key: str | None,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region or "us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self.bucket)

    def put_bytes(self, workspace_id, kind, data, *, content_type=None, ext=""):  # type: ignore[override]
        digest = sha256_hex(data)
        ref = ArtifactRef.build(
            workspace_id, kind, digest, len(data), _guess(content_type, ext), ext
        )
        if not self.exists(workspace_id, ref.key):
            self._s3.put_object(
                Bucket=self.bucket,
                Key=ref.key,
                Body=data,
                ContentType=ref.content_type,
                Metadata={"sha256": digest},
            )
        return ref

    def put_file(self, workspace_id, kind, path, *, content_type=None):  # type: ignore[override]
        return self.put_bytes(
            workspace_id,
            kind,
            path.read_bytes(),
            content_type=content_type,
            ext=path.suffix.lstrip("."),
        )

    def get_bytes(self, workspace_id, key):  # type: ignore[override]
        _validate_key(key, workspace_id)
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def open(self, workspace_id, key):  # type: ignore[override]
        import io

        return io.BytesIO(self.get_bytes(workspace_id, key))

    def exists(self, workspace_id, key):  # type: ignore[override]
        from botocore.exceptions import ClientError

        _validate_key(key, workspace_id)
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def list_keys(self, workspace_id, kind=None):  # type: ignore[override]
        prefix = f"{workspace_id}/" + (f"{kind}/" if kind else "")
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def delete_workspace(self, workspace_id):  # type: ignore[override]
        keys = self.list_keys(workspace_id)
        for i in range(0, len(keys), 1000):
            self._s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]], "Quiet": True},
            )
        return len(keys)


def open_store(root: Path | None = None) -> ArtifactStore:
    """The artifact store `object_store.backend` selects.

    Every caller must come through here, or the backend setting is a lie. `root` overrides the
    filesystem location for callers that own a per-run directory (stage contexts, the demo
    runner); it is meaningless for object storage and ignored when the backend is s3.
    """
    from content_factory.config import get_settings

    s = get_settings().object_store
    if s.backend == "filesystem":
        return FilesystemArtifactStore(root or Path(s.bucket))
    return S3ArtifactStore(
        s.bucket,
        endpoint_url=s.endpoint_url,
        region=s.region,
        access_key=os.environ.get(s.access_key_env),
        secret_key=os.environ.get(s.secret_key_env),
    )
