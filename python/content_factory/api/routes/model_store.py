"""/v1/models/*: the model store, as the browser sees it.

One place answers "what does this machine need, what has it got, and what will one click do about
it": the declared requirements of every workflow, resolved against the pinned registry
(:mod:`content_factory.models.weights`) and against what is actually on disk, plus the install and
relink calls that change it. Reads are viewer-level; installing is editor-level and never accepts
a URL — the caller names a registry key and the pinned source is looked up here, so nothing a
browser (or a model) sends can redirect a download somewhere else.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, app_settings, get_db, require_owner, require_role
from content_factory.db.base import new_id
from content_factory.db.models import ConnectedAccount, Role
from content_factory.models.weight_install import (
    InstallError,
    WeightInstaller,
    catalog_status,
    index_root,
    store_space,
)
from content_factory.models.weights import package_for_requirement, skill_env_by_key
from content_factory.schemas.base import SchemaModel
from content_factory.services import audit
from content_factory.workflows.catalog import load_definitions

router = APIRouter(prefix="/v1/models", tags=["models"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


class InstallRequest(SchemaModel):
    key: str = Field(min_length=1, max_length=120)
    """A registry key (``ltx-2.5``) or a skill env key (``skill:skills/audio/qwen3tts``)."""


class HuggingFaceTokenBody(SchemaModel):
    token: str = Field(min_length=8, max_length=400)
    """A Hugging Face access token. Sealed in the vault; never returned, logged or put in argv."""


HF_PLATFORM = "huggingface"
HF_AAD = "access"


async def _hf_account(db: AsyncSession, workspace_id: str) -> ConnectedAccount | None:
    return (
        await db.execute(
            select(ConnectedAccount).where(
                ConnectedAccount.workspace_id == workspace_id,
                ConnectedAccount.platform == HF_PLATFORM,
            )
        )
    ).scalar_one_or_none()


async def _hf_token(db: AsyncSession, workspace_id: str) -> str | None:
    """The stored token, opened for exactly one install. Absent is normal: only gated
    repositories need it, and this host may have none of them."""
    from content_factory.security.vault import Sealed, TokenVault, VaultError

    account = await _hf_account(db, workspace_id)
    if account is None:
        return None
    try:
        return TokenVault().open(
            Sealed(account.token_key_id, account.token_nonce, account.token_ciphertext),
            aad=f"{workspace_id}|{HF_PLATFORM}|{HF_AAD}",
        )
    except VaultError:
        # A key-ring rotation that lost the old key, or a tampered row: the install should say
        # "gated, no usable token" rather than die on a decryption error.
        return None


@lru_cache(maxsize=1)
def _demand() -> dict[str, dict[str, Any]]:
    """Which workflows want each registry key, and whether any of them treats it as required.

    Cached: the workflow definitions are files on disk that only change when the operator edits
    them and reruns `just schemas`, and this endpoint is polled by an open browser tab.
    """
    demand: dict[str, dict[str, Any]] = {}
    for template in load_definitions().values():
        for req in template.models:
            if req.kind == "skill":
                env = skill_env_by_key(req.skill)
                key = env.key if env else ""
            else:
                package = package_for_requirement(req)
                key = package.key if package else ""
            if not key:
                continue
            entry = demand.setdefault(key, {"wanted_by": [], "required": False, "labels": []})
            if template.id not in entry["wanted_by"]:
                entry["wanted_by"].append(template.id)
            if req.label not in entry["labels"]:
                entry["labels"].append(req.label)
            entry["required"] = entry["required"] or not req.optional
    return demand


def _installer(request: Request) -> WeightInstaller:
    installer = getattr(request.app.state, "weight_installer", None)
    if not isinstance(installer, WeightInstaller):
        installer = WeightInstaller(settings=app_settings(request))
        request.app.state.weight_installer = installer
    return installer


# Sync handlers on purpose: they stat the weight store and start subprocesses, which belongs in
# the threadpool rather than on the event loop.
@router.get("/catalog")
def catalog(request: Request, p: Principal = Depends(VIEWER)) -> dict[str, Any]:
    settings = app_settings(request)
    packages, envs = catalog_status(settings=settings)
    installer = _installer(request)
    demand = _demand()
    free_bytes, total_bytes = store_space(installer.store)
    return {
        "store": str(installer.store),
        "store_exists": installer.store.is_dir(),
        "store_free_bytes": free_bytes,
        "store_total_bytes": total_bytes,
        "index_root": str(index_root()),
        "comfy_models_dir": str(installer.comfy) if installer.comfy else "",
        "packages": [
            {
                **p_status.as_dict(),
                **demand.get(
                    p_status.package.key, {"wanted_by": [], "required": False, "labels": []}
                ),
            }
            for p_status in packages
        ],
        "skill_envs": [
            {
                **e.as_dict(),
                **demand.get(e.env.key, {"wanted_by": [], "required": False, "labels": []}),
            }
            for e in envs
        ],
        "jobs": [job.as_dict() for job in installer.jobs()],
    }


@router.post("/install", status_code=status.HTTP_202_ACCEPTED)
async def install(
    body: InstallRequest,
    request: Request,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    token = await _hf_token(db, p.workspace_id)
    try:
        job = _installer(request).start(body.key, hf_token=token)
    except InstallError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err
    return job.as_dict()


@router.get("/hf-access")
async def hf_access(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Whether a Hugging Face token is stored, and for whom. Never the token itself."""
    account = await _hf_account(db, p.workspace_id)
    return {
        "present": account is not None,
        "handle": account.handle if account else "",
        "stored_at": account.created_at.isoformat() if account else None,
    }


@router.put("/hf-access", status_code=status.HTTP_204_NO_CONTENT)
async def put_hf_access(
    body: HuggingFaceTokenBody,
    p: Principal = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Store (or replace) the token gated repositories need. Owner only: it is a credential.

    Kept in the same sealed columns as every other connected credential, with the same
    workspace-bound AAD, so it cannot be read by another workspace or decrypted out of a
    database dump alone.
    """
    from content_factory.security.vault import TokenVault

    sealed = TokenVault().seal(body.token.strip(), aad=f"{p.workspace_id}|{HF_PLATFORM}|{HF_AAD}")
    account = await _hf_account(db, p.workspace_id)
    if account is None:
        db.add(
            ConnectedAccount(
                id=new_id("acc"),
                workspace_id=p.workspace_id,
                platform=HF_PLATFORM,
                handle="huggingface.co",
                scopes=["model-download"],
                token_key_id=sealed.key_id,
                token_nonce=sealed.nonce_b64,
                token_ciphertext=sealed.ciphertext_b64,
            )
        )
    else:
        account.token_key_id = sealed.key_id
        account.token_nonce = sealed.nonce_b64
        account.token_ciphertext = sealed.ciphertext_b64
    await db.commit()
    await audit.record(
        db,
        "models.hf_token.store",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="connected_account",
        target_id=HF_PLATFORM,
    )


@router.delete("/hf-access", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hf_access(
    p: Principal = Depends(require_owner), db: AsyncSession = Depends(get_db)
) -> None:
    account = await _hf_account(db, p.workspace_id)
    if account is not None:
        await db.delete(account)
        await db.commit()
        await audit.record(
            db,
            "models.hf_token.delete",
            actor_account_id=p.account.id,
            workspace_id=p.workspace_id,
            target_type="connected_account",
            target_id=HF_PLATFORM,
        )


@router.get("/jobs")
def jobs(request: Request, p: Principal = Depends(VIEWER)) -> list[dict[str, Any]]:
    return [job.as_dict() for job in _installer(request).jobs()]


@router.post("/relink")
def relink(request: Request, p: Principal = Depends(EDITOR)) -> dict[str, list[str]]:
    """Re-create every index and ComfyUI symlink for weights already on disk."""
    return _installer(request).relink()
