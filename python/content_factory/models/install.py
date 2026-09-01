"""Allowlisted ModelManager (18.8): compare inventory to a signed plan; emit `comfy model download`
argument arrays; verify hashes; never download from a model-generated URL."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from content_factory.schemas.base import SchemaModel, Sha256Hex

ALLOWED_MODEL_HOSTS = ("huggingface.co", "civitai.com")


class ModelInstallError(Exception):
    pass


class ModelFileSpec(SchemaModel):
    filename: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    relative_path: str = Field(pattern=r"^models/[a-z_]+$")
    source_url: str = Field(min_length=1)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=1)
    license: str = Field(min_length=1)


class ModelInstallPlan(SchemaModel):
    plan_id: str = Field(min_length=1)
    files: tuple[ModelFileSpec, ...] = Field(min_length=1)
    total_download_bytes: int = Field(ge=0)


@dataclass(frozen=True)
class InstallAction:
    file: ModelFileSpec
    reason: str  # missing | hash_mismatch
    command: tuple[str, ...]  # argument array, never a shell string


def validate_plan(plan: ModelInstallPlan) -> None:
    for f in plan.files:
        host = urlparse(f.source_url).hostname or ""
        if urlparse(f.source_url).scheme != "https":
            raise ModelInstallError(f"{f.filename}: model downloads must use https")
        if not any(host == d or host.endswith("." + d) for d in ALLOWED_MODEL_HOSTS):
            raise ModelInstallError(
                f"{f.filename}: host {host!r} is not an allowlisted model source"
            )
        if ".." in f.filename or "/" in f.filename:
            raise ModelInstallError(f"{f.filename}: invalid filename")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_actions(plan: ModelInstallPlan, workspace: Path) -> list[InstallAction]:
    """What `comfy model download` calls are needed. Verification only — nothing is executed."""
    validate_plan(plan)
    actions: list[InstallAction] = []
    for f in plan.files:
        dest = workspace / f.relative_path / f.filename
        reason = ""
        if not dest.exists():
            reason = "missing"
        elif file_sha256(dest) != f.sha256:
            reason = "hash_mismatch"
        if reason:
            actions.append(
                InstallAction(
                    file=f,
                    reason=reason,
                    command=(
                        "comfy",
                        f"--workspace={workspace}",
                        "model",
                        "download",
                        "--url",
                        f.source_url,
                        "--relative-path",
                        f.relative_path,
                        "--filename",
                        f.filename,
                    ),
                )
            )
    return actions


def verify_installed(plan: ModelInstallPlan, workspace: Path) -> dict[str, bool]:
    return {
        f.filename: (workspace / f.relative_path / f.filename).exists()
        and file_sha256(workspace / f.relative_path / f.filename) == f.sha256
        for f in plan.files
    }
