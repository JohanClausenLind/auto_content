"""Model inventory: root resolution (settings + comfy-cli ini + extras) and read-only scanning."""

from __future__ import annotations

from pathlib import Path

from content_factory.comfyui.inventory import (
    comfy_cli_default_workspace,
    resolve_roots,
    scan_models,
)
from content_factory.config import load_settings


def make_cli_ini(tmp_path: Path, workspace: Path) -> Path:
    ini = tmp_path / "config.ini"
    ini.write_text(f"[DEFAULT]\nenable_tracking = False\ndefault_workspace = {workspace}\n")
    return ini


def test_comfy_cli_workspace_parsed_and_missing_file_is_none(tmp_path: Path) -> None:
    ws = tmp_path / "ComfyUI"
    assert comfy_cli_default_workspace(make_cli_ini(tmp_path, ws)) == ws
    assert comfy_cli_default_workspace(tmp_path / "nope.ini") is None


def test_resolve_roots_dedupes_and_orders(tmp_path: Path) -> None:
    ws = tmp_path / "ComfyUI"
    (ws / "models").mkdir(parents=True)
    extra = tmp_path / "ai_models"
    extra.mkdir()
    settings = load_settings(
        comfyui={
            "workspace": str(ws),
            "managed_by_comfy_cli": True,
            "extra_model_roots": (str(extra), str(ws / "models")),  # duplicate of settings root
        }
    )
    roots = resolve_roots(settings, comfy_cli_config=make_cli_ini(tmp_path, ws))
    assert [(r.source, r.exists) for r in roots] == [("settings", True), ("extra", True)]
    assert roots[0].path == (ws / "models").resolve()


def test_scan_lists_model_files_by_kind_and_ignores_junk(tmp_path: Path) -> None:
    models = tmp_path / "models"
    (models / "loras").mkdir(parents=True)
    (models / "vae").mkdir()
    (models / ".hidden").mkdir()
    (models / "loras" / "style.safetensors").write_bytes(b"x" * 10)
    (models / "vae" / "video_vae.safetensors").write_bytes(b"y" * 20)
    (models / "vae" / "notes.txt").write_text("not a model")
    (models / ".hidden" / "sneaky.safetensors").write_bytes(b"z")
    (models / "top.gguf").write_bytes(b"g" * 5)
    # deeper than MAX_DEPTH: pruned
    deep = models / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "too_deep.safetensors").write_bytes(b"n")

    settings = load_settings(comfyui={"workspace": str(tmp_path), "managed_by_comfy_cli": False})
    files = scan_models(resolve_roots(settings))
    listed = {(f.kind, f.filename, f.size_bytes) for f in files}
    assert ("loras", "style.safetensors", 10) in listed
    assert ("vae", "video_vae.safetensors", 20) in listed
    assert ("models", "top.gguf", 5) in listed  # top-level files take the root dir name
    assert all(f.filename != "sneaky.safetensors" for f in files)
    assert all(f.filename != "too_deep.safetensors" for f in files)
    assert all(f.filename != "notes.txt" for f in files)


def test_missing_roots_scan_to_empty(tmp_path: Path) -> None:
    settings = load_settings(
        comfyui={"workspace": str(tmp_path / "missing"), "managed_by_comfy_cli": False}
    )
    roots = resolve_roots(settings)
    assert roots[0].exists is False
    assert scan_models(roots) == []
